//! The tests' own bus (LP_TEST_BUS): where it names a file, the panels plugged in are those the
//! file lists, in place of what the system lists, and none where it is not there. A line is a
//! model's id for a panel on USB, or a port, "=" and a model's id for one on a serial port
//! ("COM7=turing-3.5"). The file is read each time the bus is asked, so a test can plug one in
//! and pull it out while the app runs (as the Python app's tests make up its bus in its process).

use crate::models::{find_model, PanelModel};
use crate::serial::PortInfo;
use crate::usb::Found;

/// What is plugged in, where the tests make up the bus: (port, model) for each line.
fn plugged() -> Option<Vec<(Option<String>, &'static PanelModel)>> {
    let file = std::env::var_os("LP_TEST_BUS")?;
    let text = std::fs::read_to_string(file).unwrap_or_default();
    let mut found = Vec::new();
    for line in text.lines().map(str::trim).filter(|line| !line.is_empty()) {
        let (port, id) = match line.split_once('=') {
            Some((port, id)) => (Some(port.trim().to_string()), id.trim()),
            None => (None, line),
        };
        if let Some(model) = find_model(id) {
            found.push((port, model));
        }
    }
    Some(found)
}

/// The USB devices of the tests' bus, as usb::list makes them (every panel that is not on a
/// port, of the vendor `vendor`).
pub(crate) fn usb(vendor: u16) -> Option<Vec<Found>> {
    let plugged = plugged()?;
    let mut found = Vec::new();
    for (port, model) in plugged {
        let Some(&(vid, pid)) = model.usb_ids.first() else {
            continue;
        };
        if port.is_none() && vid == vendor {
            let address = u8::try_from(found.len() + 1).unwrap_or(u8::MAX);
            found.push(Found {
                pid,
                bus: "test".into(),
                busnum: None,
                address,
                ports: vec![address],
            });
        }
    }
    Some(found)
}

/// The ids of every device of the tests' bus, as usb::all_ids lists them.
pub(crate) fn ids() -> Option<Vec<(u16, u16)>> {
    let plugged = plugged()?;
    Some(
        plugged
            .iter()
            .filter_map(|(_, model)| model.usb_ids.first().copied())
            .collect(),
    )
}

/// The serial ports of the tests' bus, as serial::ports lists them.
pub(crate) fn ports() -> Option<Vec<PortInfo>> {
    let plugged = plugged()?;
    Some(
        plugged
            .into_iter()
            .filter_map(|(port, model)| {
                let device = port?;
                let ids = model.usb_ids.first().copied();
                Some(PortInfo {
                    device,
                    vid: ids.map(|(vid, _)| vid),
                    pid: ids.map(|(_, pid)| pid),
                    serial_number: model.serial_numbers.first().map(|s| (*s).to_string()),
                    description: "n/a".to_string(),
                })
            })
            .collect(),
    )
}
