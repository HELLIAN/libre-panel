//! The light ring of Lian Li's Universal Screen 8.8": the 60 LEDs round the screen, a USB
//! device of their own (`0416:8050`) beside the panel (`1CBE:A088`) behind the screen's hub.
//!
//! As the Lian Li Universal Screen 8.8" USB protocol reference describes it (immortalbob,
//! github.com/immortalbob/lian-li-universal-screen-protocol, CC BY 4.0: written from USB
//! captures of L-Connect 3 and bench tests of the ring; this driver follows its sections 2 to
//! 7 and 11): a vendor-class device (not HID) with one interface, a bulk OUT endpoint `0x01`
//! and a bulk IN endpoint `0x81`, and plain 64-byte packets without a header, a checksum or
//! encryption. Two commands are known, and only they are safe:
//!
//! - `0x11` sets 20 LEDs: `11`, the first LED (0, 20 or 40), `00 00`, then 20 × R, G, B. A
//!   frame is three of them; each takes effect on its own, and the ring answers none. It keeps
//!   showing the last frame when nothing more comes, so a colour is written once.
//! - `0x10` asks the ring's firmware version: `10` and 63 zeros; the answer is `10`, the major
//!   and the minor number and zeros (`lianliLed88_1_3` for 1 and 3).
//!
//! Every other first byte reaches firmware erase, write or reboot code on this ring or its
//! siblings, so packets are made in one place ([`Packet`]) that can make no other. The ring
//! has no brightness, effect, save or off command: every effect is frames the host sends, and
//! off is a black frame. Only one program can have the ring at a time (WinUSB on Windows):
//! while L-Connect or OpenRGB has it, Libre Panel is told the device is busy and leaves it.

use std::fmt;

use crate::base::{leave_place, take_place};
use crate::usb_link::{self, NusbIo, UsbError, UsbIo, UsbResult};

pub const VENDOR_ID: u16 = 0x0416;
/// (the vendor id is shared with other Lian Li controllers and Thermalright's screens: the
/// ring is told by both ids, never by the vendor's alone)
pub const PRODUCT_ID: u16 = 0x8050;
/// the LEDs of the ring, in the order of the wire
pub const LEDS: usize = 60;
const PER_PACKET: usize = 20;
const PACKET_SIZE: usize = 64;
const INTERFACE: u8 = 0;
const OUT: u8 = 0x01;
const IN: u8 = 0x81;
/// how long a packet may take on its way (one goes in about a millisecond)
const WRITE_MS: u64 = 500;
/// how long the version's answer may take (it came after 0.2 s)
const VERSION_MS: u64 = 400;

/// One colour for each LED, in the order of the wire.
pub type Frame = [[u8; 3]; LEDS];

/// The third of a frame a `0x11` packet carries.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Third {
    First,
    Second,
    Last,
}

impl Third {
    const ALL: [Third; 3] = [Third::First, Third::Second, Third::Last];

    fn first_led(self) -> usize {
        match self {
            Third::First => 0,
            Third::Second => PER_PACKET,
            Third::Last => 2 * PER_PACKET,
        }
    }
}

/// What may go to the ring: the two commands that are known, in the layout that is known.
#[derive(Clone, Copy, Debug)]
pub enum Packet<'a> {
    /// `0x11`: 20 LEDs of a frame
    SetLeds(Third, &'a Frame),
    /// `0x10`: the firmware's version, asked
    GetVersion,
}

impl Packet<'_> {
    /// The 64 bytes on the wire.
    pub fn bytes(&self) -> [u8; PACKET_SIZE] {
        let mut packet = [0u8; PACKET_SIZE];
        match self {
            Packet::GetVersion => packet[0] = 0x10,
            Packet::SetLeds(third, frame) => {
                let first = third.first_led();
                packet[0] = 0x11;
                packet[1] = first as u8;
                // (bytes 2 and 3 stay 00 00, as every packet L-Connect sent has them)
                for (i, colour) in frame[first..first + PER_PACKET].iter().enumerate() {
                    packet[4 + 3 * i..7 + 3 * i].copy_from_slice(colour);
                }
            }
        }
        packet
    }
}

/// Why the ring could not be had.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Unavailable {
    /// none on the bus
    Absent,
    /// another program has it (L-Connect, OpenRGB)
    Busy,
    /// this user may not use it (Linux without the setup's rule)
    Denied,
    /// a device with its ids that is not as the ring is described
    Unlike(String),
    Failed(String),
}

impl fmt::Display for Unavailable {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Unavailable::Absent => f.write_str("no light ring on the USB bus"),
            Unavailable::Busy => f.write_str("another program has the light ring"),
            Unavailable::Denied => f.write_str("this user may not use the light ring"),
            Unavailable::Unlike(why) | Unavailable::Failed(why) => f.write_str(why),
        }
    }
}

impl From<UsbError> for Unavailable {
    fn from(error: UsbError) -> Unavailable {
        match error {
            UsbError::Busy => Unavailable::Busy,
            UsbError::Denied => Unavailable::Denied,
            other => Unavailable::Failed(other.to_string()),
        }
    }
}

/// present: whether a light ring is on the bus (none is opened).
pub fn present() -> bool {
    usb_link::present(VENDOR_ID, &[PRODUCT_ID]).is_some()
}

/// The light ring, opened.
pub struct Ring {
    io: Box<dyn UsbIo>,
    /// its firmware's version ("1.3"); "" where it did not say
    pub version: String,
}

impl Ring {
    /// open: the first light ring on the bus that this process does not have open.
    pub fn open() -> Result<Ring, Unavailable> {
        let found =
            usb_link::find(VENDOR_ID, &[PRODUCT_ID]).map_err(|why| Unavailable::Failed(why.0))?;
        if found.is_empty() {
            return Err(Unavailable::Absent);
        }
        let mut busy = false;
        for candidate in found {
            let place = candidate.place();
            if !take_place(&place) {
                busy = true;
                continue;
            }
            return match NusbIo::open_found(&candidate, place.clone()) {
                Ok(io) => Ring::with(Box::new(io)),
                Err(error) => {
                    leave_place(&place);
                    Err(error.into())
                }
            };
        }
        match busy {
            true => Err(Unavailable::Busy),
            false => Err(Unavailable::Absent),
        }
    }

    /// with: the ring behind `io`, checked to be as it is described (one interface, bulk
    /// endpoints `0x01` and `0x81` of 64 bytes), its interface claimed (and nothing else set:
    /// no alternate setting is chosen) and its version asked.
    pub fn with(mut io: Box<dyn UsbIo>) -> Result<Ring, Unavailable> {
        let interfaces = io.interfaces();
        let Some(interface) = interfaces.iter().find(|i| i.number == INTERFACE) else {
            return Err(Unavailable::Unlike(
                "the light ring has no interface 0".into(),
            ));
        };
        for address in [OUT, IN] {
            let endpoint = interface.endpoints.iter().find(|e| e.address == address);
            if !endpoint.is_some_and(|e| !e.interrupt && e.max_packet == PACKET_SIZE) {
                return Err(Unavailable::Unlike(format!(
                    "the light ring has no bulk endpoint 0x{address:02x} of {PACKET_SIZE} bytes"
                )));
            }
        }
        io.claim(&[INTERFACE])?;
        let mut ring = Ring {
            io,
            version: String::new(),
        };
        ring.version = ring.ask_version();
        Ok(ring)
    }

    fn send(&mut self, packet: Packet) -> UsbResult<()> {
        self.io.write(OUT, &packet.bytes(), WRITE_MS)
    }

    /// The firmware's version ("1.3"), or "" where the ring does not answer as it should
    /// (nothing depends on it: the answer is checked, not needed).
    fn ask_version(&mut self) -> String {
        // (what may wait from before is read first, so it is not taken for the answer)
        let _ = self.io.read(IN, PACKET_SIZE, 20);
        if self.send(Packet::GetVersion).is_err() {
            return String::new();
        }
        match self.io.read(IN, PACKET_SIZE, VERSION_MS) {
            Ok(answer) => version_of(&answer),
            Err(_) => String::new(),
        }
    }

    /// show: `frame` on the ring, its three packets one after the other.
    pub fn show(&mut self, frame: &Frame) -> UsbResult<()> {
        for third in Third::ALL {
            self.send(Packet::SetLeds(third, frame))?;
        }
        Ok(())
    }
}

impl Drop for Ring {
    fn drop(&mut self) {
        self.io.release();
    }
}

/// version_of: the version an answer to `0x10` says ("1.3"); "" for one that is not an answer
/// (shorter than 3 bytes, another first byte, a major number of 0).
pub fn version_of(answer: &[u8]) -> String {
    match answer {
        [0x10, major, minor, ..] if *major > 0 => format!("{major}.{minor}"),
        _ => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::usb_link::{EndpointInfo, InterfaceInfo};
    use std::sync::{Arc, Mutex};

    fn hex(bytes: &[u8]) -> String {
        bytes
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<Vec<_>>()
            .join(" ")
    }

    /// what went to the ring: each packet with its endpoint
    type Written = Arc<Mutex<Vec<(u8, Vec<u8>)>>>;

    /// A ring the tests make up: what was written to it, and what it answers.
    struct Made {
        written: Written,
        answers: Vec<Vec<u8>>,
        interfaces: Vec<InterfaceInfo>,
        claimed: Arc<Mutex<Vec<u8>>>,
    }

    fn ring_interfaces() -> Vec<InterfaceInfo> {
        let endpoint = |address| EndpointInfo {
            address,
            max_packet: 64,
            interrupt: false,
        };
        vec![InterfaceInfo {
            number: 0,
            class: 0xFF,
            endpoints: vec![endpoint(0x81), endpoint(0x01)],
        }]
    }

    impl UsbIo for Made {
        fn product(&mut self) -> String {
            "8.8\" LED-1.0".into()
        }
        fn interfaces(&self) -> Vec<InterfaceInfo> {
            self.interfaces.clone()
        }
        fn claim(&mut self, interfaces: &[u8]) -> UsbResult<()> {
            self.claimed.lock().unwrap().extend_from_slice(interfaces);
            Ok(())
        }
        fn write(&mut self, endpoint: u8, data: &[u8], _: u64) -> UsbResult<()> {
            self.written.lock().unwrap().push((endpoint, data.to_vec()));
            Ok(())
        }
        fn read(&mut self, endpoint: u8, _: usize, _: u64) -> UsbResult<Vec<u8>> {
            assert_eq!(endpoint, 0x81);
            // (nothing waits before the question; the answer comes after it)
            let asked = self
                .written
                .lock()
                .unwrap()
                .iter()
                .any(|(_, p)| p[0] == 0x10);
            match asked && !self.answers.is_empty() {
                true => Ok(self.answers.remove(0)),
                false => Err(UsbError::Timeout),
            }
        }
        fn control_out(&mut self, _: u8, _: u8, _: u16, _: u16, _: &[u8], _: u64) -> UsbResult<()> {
            panic!("the ring is sent no control transfer");
        }
        fn control_in(
            &mut self,
            _: u8,
            _: u8,
            _: u16,
            _: u16,
            _: u16,
            _: u64,
        ) -> UsbResult<Vec<u8>> {
            panic!("the ring is sent no control transfer");
        }
        fn release(&mut self) {}
    }

    fn made(answers: Vec<Vec<u8>>) -> (Made, Written) {
        let written = Arc::new(Mutex::new(Vec::new()));
        let io = Made {
            written: Arc::clone(&written),
            answers,
            interfaces: ring_interfaces(),
            claimed: Arc::new(Mutex::new(Vec::new())),
        };
        (io, written)
    }

    /// TV1 of the reference: LED i = (i, i+1, i+2), its three packets byte for byte.
    #[test]
    fn a_frame_is_three_packets_as_the_reference_writes_them() {
        let mut frame: Frame = [[0; 3]; LEDS];
        for (i, led) in frame.iter_mut().enumerate() {
            *led = [i as u8, i as u8 + 1, i as u8 + 2];
        }
        let packets: Vec<String> = Third::ALL
            .iter()
            .map(|third| hex(&Packet::SetLeds(*third, &frame).bytes()))
            .collect();
        assert_eq!(packets[0], "11 00 00 00 00 01 02 01 02 03 02 03 04 03 04 05 04 05 06 05 06 07 06 07 08 07 08 09 08 09 0a 09 0a 0b 0a 0b 0c 0b 0c 0d 0c 0d 0e 0d 0e 0f 0e 0f 10 0f 10 11 10 11 12 11 12 13 12 13 14 13 14 15");
        assert_eq!(packets[1], "11 14 00 00 14 15 16 15 16 17 16 17 18 17 18 19 18 19 1a 19 1a 1b 1a 1b 1c 1b 1c 1d 1c 1d 1e 1d 1e 1f 1e 1f 20 1f 20 21 20 21 22 21 22 23 22 23 24 23 24 25 24 25 26 25 26 27 26 27 28 27 28 29");
        assert_eq!(packets[2], "11 28 00 00 28 29 2a 29 2a 2b 2a 2b 2c 2b 2c 2d 2c 2d 2e 2d 2e 2f 2e 2f 30 2f 30 31 30 31 32 31 32 33 32 33 34 33 34 35 34 35 36 35 36 37 36 37 38 37 38 39 38 39 3a 39 3a 3b 3a 3b 3c 3b 3c 3d");
    }

    /// TV2, TV3 and TV5: off, static red and the version's question; and TV6: nothing else
    /// can be made (every packet is 64 bytes, its first byte 0x10 or 0x11, its offset 0, 20
    /// or 40, its bytes 2 and 3 zero).
    #[test]
    fn off_red_and_the_version_question_are_the_references_and_nothing_else_is_made() {
        let off: Frame = [[0; 3]; LEDS];
        let red: Frame = [[0xFF, 0, 0]; LEDS];
        for (third, offset) in Third::ALL.iter().zip([0x00u8, 0x14, 0x28]) {
            let packet = Packet::SetLeds(*third, &off).bytes();
            assert_eq!(packet[..4], [0x11, offset, 0, 0]);
            assert!(packet[4..].iter().all(|b| *b == 0));
            let packet = Packet::SetLeds(*third, &red).bytes();
            assert_eq!(packet[..4], [0x11, offset, 0, 0]);
            assert!(packet[4..].chunks(3).all(|c| c == [0xFF, 0, 0]));
        }
        let question = Packet::GetVersion.bytes();
        assert_eq!(question[0], 0x10);
        assert!(question[1..].iter().all(|b| *b == 0));
        assert_eq!(question.len(), 64);
    }

    #[test]
    fn the_version_is_read_from_a_proper_answer_only() {
        let answer = |bytes: &[u8]| {
            let mut full = bytes.to_vec();
            full.resize(64, 0);
            full
        };
        assert_eq!(version_of(&answer(&[0x10, 1, 3])), "1.3");
        assert_eq!(version_of(&answer(&[0x10, 0xFF, 0])), "255.0");
        assert_eq!(version_of(&answer(&[0x11, 1, 3])), "");
        assert_eq!(version_of(&answer(&[0x10, 0, 3])), "");
        assert_eq!(version_of(&[0x10, 1]), "");
    }

    /// Opened: interface 0 claimed, the version asked once and read; a frame is three packets
    /// to 0x01; nothing but 0x10 and 0x11 goes out.
    #[test]
    fn the_ring_is_opened_asked_its_version_and_shown_a_frame() {
        let mut answer = vec![0x10, 1, 3];
        answer.resize(64, 0);
        let (io, written) = made(vec![answer]);
        let claimed = Arc::clone(&io.claimed);
        let mut ring = Ring::with(Box::new(io)).expect("opened");
        assert_eq!(ring.version, "1.3");
        assert_eq!(*claimed.lock().unwrap(), vec![0]);
        ring.show(&[[1, 2, 3]; LEDS]).expect("shown");
        let written = written.lock().unwrap();
        let firsts: Vec<(u8, u8, u8)> = written.iter().map(|(e, p)| (*e, p[0], p[1])).collect();
        assert_eq!(
            firsts,
            vec![
                (0x01, 0x10, 0),
                (0x01, 0x11, 0),
                (0x01, 0x11, 20),
                (0x01, 0x11, 40)
            ]
        );
        assert!(written.iter().all(|(_, p)| p.len() == 64));
    }

    /// A ring that does not answer the version is opened all the same; a device with its ids
    /// but other endpoints is not taken for it.
    #[test]
    fn a_silent_ring_is_opened_and_a_device_unlike_it_is_not() {
        let (io, _) = made(Vec::new());
        assert_eq!(Ring::with(Box::new(io)).expect("opened").version, "");
        let (mut io, _) = made(Vec::new());
        io.interfaces[0].endpoints[1].max_packet = 512;
        assert!(matches!(
            Ring::with(Box::new(io)),
            Err(Unavailable::Unlike(_))
        ));
        let (mut io, _) = made(Vec::new());
        io.interfaces[0].endpoints[0].interrupt = true;
        assert!(matches!(
            Ring::with(Box::new(io)),
            Err(Unavailable::Unlike(_))
        ));
    }
}
