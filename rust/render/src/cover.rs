//! The colour a cover carries (render/cover.py): what a card's frame takes on while a track
//! plays; and its colours, the light of a light field in a cover's colours takes (field.rs's
//! TINTED). The picture reduced to a few colours, and the one that joins how much of it there
//! is with how colourful it is wins; too dark and too pale ones are left out, a cover of greys
//! carries none, and a winner too dark for a thin line on a dark panel is brightened. Its
//! colours: the first [`LIGHTS`] candidates in that order, each as bright as it can be.

use std::cell::RefCell;

use lp_pixels::{Filter, Image, Mode};

/// The cover is looked at this small.
const SIDE: usize = 48;
const COLOURS: usize = 8;
/// A candidate whose brightest channel is below this carries nothing ...
const DARKEST: u8 = 60;
/// ... and one less colourful than this is grey.
const GREYEST: f64 = 0.18;
/// The winner is brightened to this, as a line on dark ground.
const BRIGHT: u8 = 180;
/// The colours of a cover a light takes.
pub const LIGHTS: usize = 3;

/// How many pictures' colours are kept (cover.py's KEPT).
const KEPT: usize = 8;

/// A picture by its bytes: its mode, its size and a 64-bit print of its pixels.
type Print = (&'static str, usize, usize, u64);

thread_local! {
    static FOUND: RefCell<Vec<(Print, Vec<[u8; 3]>)>> = const { RefCell::new(Vec::new()) };
}

/// dominant: the colour that carries `image`, or None (only greys, or a picture that cannot
/// be read so).
pub fn dominant(image: &Image) -> Option<[u8; 3]> {
    candidates(image).first().map(|&c| brightened(c, BRIGHT))
}

/// colours: the colours of `image` a light takes: its first [`LIGHTS`] candidates, each as
/// bright as a colour of it can be; none for only greys.
pub fn colours(image: &Image) -> Vec<[u8; 3]> {
    let found = candidates(image);
    found
        .iter()
        .take(LIGHTS)
        .map(|&c| {
            // (whole numbers: the brightest channel becomes 255 itself)
            let top = u32::from(c.into_iter().max().unwrap_or(1).max(1));
            c.map(|v| (u32::from(v) * 255 / top) as u8)
        })
        .collect()
}

/// _brightened: `colour` brightened until its brightest channel is about `to`, where it is
/// below.
fn brightened(colour: [u8; 3], to: u8) -> [u8; 3] {
    let top = colour.into_iter().max().unwrap_or(0);
    if top >= to {
        return colour;
    }
    let k = f64::from(to) / f64::from(top);
    colour.map(|c| (f64::from(c) * k).trunc().min(255.0) as u8)
}

/// The candidates of `image` (find), worked out once per picture (cover.py keeps them by the
/// picture's object; here by its bytes, a print of them read in a twentieth of the time they
/// take): the [`KEPT`] last asked.
fn candidates(image: &Image) -> Vec<[u8; 3]> {
    let print = (
        image.mode().name(),
        image.width(),
        image.height(),
        print_of(image.as_bytes()),
    );
    let hit = FOUND.with(|found| {
        let mut found = found.borrow_mut();
        let at = found.iter().position(|(p, _)| *p == print)?;
        let entry = found.remove(at);
        let colours = entry.1.clone();
        found.push(entry);
        Some(colours)
    });
    if let Some(colours) = hit {
        return colours;
    }
    let colours = find(image).unwrap_or_default();
    FOUND.with(|found| {
        let mut found = found.borrow_mut();
        found.push((print, colours.clone()));
        if found.len() > KEPT {
            found.remove(0);
        }
    });
    colours
}

/// A print of `bytes`: each eight of them mixed into it in turn.
fn print_of(bytes: &[u8]) -> u64 {
    const K1: u64 = 0x9E37_79B9_7F4A_7C15;
    const K2: u64 = 0xC2B2_AE3D_27D4_EB4F;
    let mut h = (bytes.len() as u64).wrapping_mul(K2);
    let mut words = bytes.chunks_exact(8);
    for word in &mut words {
        let v = u64::from_le_bytes(word.try_into().expect("eight bytes"));
        h = (h ^ v.wrapping_mul(K1)).rotate_left(31).wrapping_mul(K2);
    }
    for &b in words.remainder() {
        h = (h ^ u64::from(b).wrapping_mul(K1))
            .rotate_left(31)
            .wrapping_mul(K2);
    }
    h ^ (h >> 29)
}

/// _find: the colours that can carry `image`, the one that carries it most first: each by how
/// much of it there is and how colourful it is (of equals, the one met first).
fn find(image: &Image) -> Option<Vec<[u8; 3]>> {
    let rgb = image.convert(Mode::RGB).ok()?;
    let small = rgb.resize(SIDE, SIDE, Filter::Bilinear, None, None).ok()?;
    let (indices, palette) = small.quantize(COLOURS).ok()?;
    let pixels: Vec<u8> = indices
        .iter()
        .flat_map(|&i| palette[usize::from(i)])
        .collect();
    let few = Image::from_bytes(Mode::RGB, SIDE, SIDE, pixels).ok()?;
    let mut scored: Vec<(f64, [u8; 3])> = Vec::new();
    for (count, colour) in few.getcolors(SIDE * SIDE).ok()?.unwrap_or_default() {
        let (r, g, b) = (colour[0], colour[1], colour[2]);
        let top = r.max(g).max(b);
        if top < DARKEST {
            continue;
        }
        let colourful = f64::from(top - r.min(g).min(b)) / f64::from(top.max(1));
        if colourful < GREYEST {
            continue;
        }
        let value = (f64::from(count) / (SIDE * SIDE) as f64).powf(0.5) * colourful;
        scored.push((value, [r, g, b]));
    }
    // (sorted as Python sorts: stable, the higher first)
    scored.sort_by(|a, b| b.0.total_cmp(&a.0));
    Some(scored.into_iter().map(|(_, colour)| colour).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn picture(r: u8, g: u8, b: u8) -> Image {
        Image::new(Mode::RGB, 64, 64, &[r, g, b])
    }

    #[test]
    fn a_pictures_colour_is_kept_by_its_bytes_and_a_new_picture_has_its_own() {
        let (red, blue) = (picture(220, 30, 40), picture(30, 40, 220));
        let first = dominant(&red);
        assert_eq!(
            first,
            find(&red).and_then(|c| c.first().map(|&c| brightened(c, BRIGHT)))
        );
        assert_eq!(dominant(&red.clone()), first); // (a copy: the same bytes, kept)
                                                   // (another picture of the same size, which a new track's cover may be, at once)
        assert_eq!(
            dominant(&blue),
            candidates(&blue).first().map(|&c| brightened(c, BRIGHT))
        );
        assert_ne!(dominant(&blue), first);
        assert_eq!(dominant(&picture(128, 128, 128)), None); // (greys carry none)
        for k in 0..KEPT as u8 {
            dominant(&picture(100 + k, 20, 20));
        }
        assert_eq!(dominant(&red), first); // (made anew once it was left out)
    }
}
