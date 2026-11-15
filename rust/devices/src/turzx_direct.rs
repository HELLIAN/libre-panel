//! A TURZX USB panel taken in this process for a few commands, without Libre Panel's engine:
//! for a panel that is on the bus only for moments (one that restarts over and over), where
//! starting the engine first costs the time there is. `libre-panel start-screen` uses it.
//!
//! Nothing is asked of the panel on the way in: its configuration is not set anew (the system
//! set it; setting it again is what a PC's USB coming up looks like to the panel), no replies
//! are read away and no sync is sent. The first packet written is the first thing the panel
//! is sent, and a write waits until the panel takes it.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

use nusb::transfer::{Buffer, Bulk, In, Out, TransferError};
use nusb::MaybeFuture;

use crate::models::PanelModel;
use crate::turzx::VENDOR_ID;
use crate::turzx_packet::{now_stamp, packet, READ_LEN};
use crate::usb;

/// A TURZX USB panel that is on the bus (not opened).
pub struct Seen {
    info: nusb::DeviceInfo,
}

/// Which panel is taken where several TURZX USB panels are on the bus.
#[derive(Clone, Debug, Default)]
pub struct Which {
    /// the models taken first, by the catalogue's ids (none: the first panel there is)
    pub models: Vec<&'static str>,
    /// no panel of another model is taken
    pub only: bool,
}

/// The panel to take that is on the bus now; None for none, or where the system cannot list
/// its USB devices.
pub fn seen(which: &Which) -> Option<Seen> {
    scan(which).0
}

/// A USB device on the bus that is not the panel taken: what the system says of it, for the
/// log of a panel that does not come back as itself.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Other {
    pub vid: u16,
    pub pid: u16,
    /// its ports from the root hub
    pub place: String,
    /// the name it gives itself, where the system has one; a TURZX USB panel's as the
    /// catalogue calls its model
    pub name: String,
    /// a TURZX USB panel (one that is not the one taken)
    pub panel: bool,
}

/// The TURZX USB model a device is by its ids; None for any other device, another maker's
/// under the same vendor id among them.
fn model_by(vid: u16, pid: u16) -> Option<&'static PanelModel> {
    match vid == VENDOR_ID {
        true => usb::model_of(pid),
        false => None,
    }
}

impl Other {
    /// A device as the bus tells it (`name`: what it calls itself).
    pub fn new(vid: u16, pid: u16, place: String, name: String) -> Other {
        let model = model_by(vid, pid);
        Other {
            vid,
            pid,
            place,
            name: model.map_or(name, |model| model.label()),
            panel: model.is_some(),
        }
    }
}

fn ports_of(info: &nusb::DeviceInfo) -> String {
    let ports: Vec<String> = (info.port_chain().iter()).map(u8::to_string).collect();
    format!("ports {}", ports.join("."))
}

/// What is on the bus now, in one look: the TURZX USB panel to take (`which`), and every
/// other device, a TURZX panel that is not taken among them under its model's name. (None,
/// nothing) where the system cannot list its USB devices.
pub fn scan(which: &Which) -> (Option<Seen>, Vec<Other>) {
    let Ok(devices) = nusb::list_devices().wait() else {
        return (None, Vec::new());
    };
    let devices: Vec<nusb::DeviceInfo> = devices.collect();
    let model = |info: &nusb::DeviceInfo| model_by(info.vendor_id(), info.product_id());
    let wanted =
        |info: &nusb::DeviceInfo| model(info).is_some_and(|model| which.models.contains(&model.id));
    let taken = (devices.iter().position(wanted)).or_else(|| match which.only {
        true => None,
        false => devices.iter().position(|info| model(info).is_some()),
    });
    let (mut panel, mut others) = (None, Vec::new());
    for (n, info) in devices.into_iter().enumerate() {
        if Some(n) == taken {
            panel = Some(Seen { info });
            continue;
        }
        let name = info.product_string().unwrap_or_default().trim().to_string();
        others.push(Other::new(
            info.vendor_id(),
            info.product_id(),
            ports_of(&info),
            name,
        ));
    }
    (panel, others)
}

impl Seen {
    pub fn pid(&self) -> u16 {
        self.info.product_id()
    }

    pub fn model(&self) -> Option<&'static PanelModel> {
        usb::model_of(self.pid())
    }

    /// Where it is, for the log: its ports from the root hub and its address.
    pub fn place(&self) -> String {
        let said = format!(
            "{} address {}",
            ports_of(&self.info),
            self.info.device_address()
        );
        // (Windows: the driver it has, WinUSB once the panel can be opened; a panel whose
        // entries were cleared from the registry has none until Windows has set it up anew)
        #[cfg(windows)]
        let said = match self.info.driver().filter(|driver| !driver.is_empty()) {
            Some(driver) => format!("{said}, driver {driver}"),
            None => format!("{said}, no driver yet"),
        };
        said
    }

    /// Interface 0 claimed and its bulk endpoints taken. An Err is worth another try at once
    /// while the panel is on the bus: Windows lists a device before its driver has started.
    pub fn open(&self) -> Result<Direct, String> {
        let device = (self.info.open().wait()).map_err(|e| format!("cannot open it: {e}"))?;
        let _ = device.detach_kernel_driver(0); // (Linux; nothing elsewhere)
        let interface = (device.claim_interface(0).wait())
            .map_err(|e| format!("cannot claim its interface: {e}"))?;
        let (mut out_address, mut in_address) = (0x01, 0x81); // (as every TURZX panel has them)
        if let Some(descriptor) = interface.descriptor() {
            let bulk = (descriptor.endpoints())
                .filter(|e| e.transfer_type() == nusb::descriptors::TransferType::Bulk);
            let (mut out_found, mut in_found) = (false, false);
            for endpoint in bulk {
                match endpoint.direction() {
                    nusb::transfer::Direction::Out if !out_found => {
                        (out_address, out_found) = (endpoint.address(), true);
                    }
                    nusb::transfer::Direction::In if !in_found => {
                        (in_address, in_found) = (endpoint.address(), true);
                    }
                    _ => {}
                }
            }
        }
        let out = (interface.endpoint::<Bulk, Out>(out_address))
            .map_err(|e| format!("cannot open its endpoint: {e}"))?;
        let input = (interface.endpoint::<Bulk, In>(in_address))
            .map_err(|e| format!("cannot open its endpoint: {e}"))?;
        Ok(Direct {
            wire: Wire::Usb {
                out,
                input,
                _interface: interface,
            },
        })
    }
}

/// Why a packet did not go out, or no reply came.
#[derive(Debug, PartialEq, Eq)]
pub enum Trouble {
    /// nothing in the time given: the panel took no packet, or sent none
    Silent,
    /// the transfer failed: the panel has left the bus, or its endpoint stalled
    Failed(String),
}

impl std::fmt::Display for Trouble {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Trouble::Silent => write!(f, "nothing in time"),
            Trouble::Failed(why) => write!(f, "{why}"),
        }
    }
}

enum Wire {
    Usb {
        out: nusb::Endpoint<Bulk, Out>,
        input: nusb::Endpoint<Bulk, In>,
        _interface: nusb::Interface,
    },
    /// the tests' simulated panel behind a socket (the engine's TcpLink: "W" len timeout data
    /// -> "K" or "E" len text; "R" len timeout -> "D" len data, "T" or "E" len text)
    Test(TcpStream),
}

/// A TURZX panel open in this process: command packets out, replies in.
pub struct Direct {
    wire: Wire,
}

fn failed(error: TransferError) -> Trouble {
    match error {
        TransferError::Cancelled => Trouble::Silent,
        other => Trouble::Failed(other.to_string()),
    }
}

impl Direct {
    /// The tests' simulated panel at `spec` ("tcp:HOST:PORT", as the engine's `--link`).
    pub fn test(spec: &str) -> Result<Direct, String> {
        let address = spec
            .strip_prefix("tcp:")
            .ok_or("the link is tcp:HOST:PORT")?;
        let stream =
            TcpStream::connect(address).map_err(|e| format!("cannot reach {address}: {e}"))?;
        let _ = stream.set_nodelay(true);
        Ok(Direct {
            wire: Wire::Test(stream),
        })
    }

    /// One command packet written, nothing after it. It waits up to `timeout_ms` for the
    /// panel to take it (a panel that is not reading yet takes it the moment it does).
    pub fn send(&mut self, cmd: u8, args: &[u8], timeout_ms: u64) -> Result<(), Trouble> {
        let data = packet(cmd, args, now_stamp()).map_err(|e| Trouble::Failed(e.to_string()))?;
        match &mut self.wire {
            Wire::Usb { out, .. } => {
                let len = data.len();
                let done = out.transfer_blocking(
                    Buffer::from(data.to_vec()),
                    Duration::from_millis(timeout_ms.max(1)),
                );
                match done.status {
                    Ok(()) if done.actual_len == len => Ok(()),
                    Ok(()) => Err(Trouble::Failed(format!(
                        "only {} of {len} bytes went out",
                        done.actual_len
                    ))),
                    Err(TransferError::Stall) => {
                        let _ = out.clear_halt().wait();
                        Err(failed(TransferError::Stall))
                    }
                    Err(e) => Err(failed(e)),
                }
            }
            Wire::Test(stream) => match asked(stream, b'W', data.len(), timeout_ms, &data)? {
                (b'K', _) => Ok(()),
                (b'T', _) => Err(Trouble::Silent),
                (_, text) => Err(Trouble::Failed(String::from_utf8_lossy(&text).into())),
            },
        }
    }

    /// One reply read (a transfer of up to 1024 bytes, as the panel's replies want it).
    pub fn read(&mut self, timeout_ms: u64) -> Result<Vec<u8>, Trouble> {
        match &mut self.wire {
            Wire::Usb { input, .. } => {
                let done = input.transfer_blocking(
                    Buffer::new(READ_LEN),
                    Duration::from_millis(timeout_ms.max(1)),
                );
                match done.status {
                    Ok(()) => {
                        let mut data = done.buffer.into_vec();
                        data.truncate(done.actual_len);
                        Ok(data)
                    }
                    Err(TransferError::Stall) => {
                        let _ = input.clear_halt().wait();
                        Err(failed(TransferError::Stall))
                    }
                    Err(e) => Err(failed(e)),
                }
            }
            Wire::Test(stream) => match asked(stream, b'R', READ_LEN, timeout_ms, &[])? {
                (b'D', data) => Ok(data),
                (b'T', _) => Err(Trouble::Silent),
                (_, text) => Err(Trouble::Failed(String::from_utf8_lossy(&text).into())),
            },
        }
    }
}

/// One exchange with the tests' simulated panel: the request's head and data, its answer.
fn asked(
    stream: &mut TcpStream,
    kind: u8,
    len: usize,
    timeout_ms: u64,
    data: &[u8],
) -> Result<(u8, Vec<u8>), Trouble> {
    let broken = |e: std::io::Error| Trouble::Failed(format!("the test link: {e}"));
    let mut head = [0u8; 9];
    head[0] = kind;
    head[1..5].copy_from_slice(&(len as u32).to_le_bytes());
    head[5..9].copy_from_slice(&(timeout_ms.min(u64::from(u32::MAX)) as u32).to_le_bytes());
    stream.write_all(&head).map_err(broken)?;
    stream.write_all(data).map_err(broken)?;
    let mut answer = [0u8; 1];
    stream.read_exact(&mut answer).map_err(broken)?;
    if answer[0] == b'K' || answer[0] == b'T' {
        return Ok((answer[0], Vec::new()));
    }
    let mut size = [0u8; 4];
    stream.read_exact(&mut size).map_err(broken)?;
    let mut body = vec![0u8; u32::from_le_bytes(size) as usize];
    stream.read_exact(&mut body).map_err(broken)?;
    Ok((answer[0], body))
}
