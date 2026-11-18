//! The colour a cover carries (render/cover.py): what a card's frame takes on while a track
//! plays. The picture reduced to a few colours, and the one that joins how much of it there is
//! with how colourful it is wins; too dark and too pale ones are left out, a cover of greys
//! carries none, and a winner too dark for a thin line on a dark panel is brightened.

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

/// How many pictures' colours are kept (cover.py's KEPT).
const KEPT: usize = 8;

/// A picture by its bytes: its mode, its size and a 64-bit print of its pixels.
type Print = (&'static str, usize, usize, u64);

thread_local! {
    static FOUND: RefCell<Vec<(Print, Option<[u8; 3]>)>> = const { RefCell::new(Vec::new()) };
}

/// dominant: the colour that carries `image`, or None (only greys, or a picture that cannot
/// be read so). Worked out once per picture (cover.py keeps it by the picture's object; here
/// by its bytes, a print of them read in a twentieth of the time the colour takes): the
/// [`KEPT`] last asked.
pub fn dominant(image: &Image) -> Option<[u8; 3]> {
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
        let colour = entry.1;
        found.push(entry);
        Some(colour)
    });
    if let Some(colour) = hit {
        return colour;
    }
    let colour = find(image);
    FOUND.with(|found| {
        let mut found = found.borrow_mut();
        found.push((print, colour));
        if found.len() > KEPT {
            found.remove(0);
        }
    });
    colour
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

/// _find: the colour that carries `image`, worked out.
fn find(image: &Image) -> Option<[u8; 3]> {
    let rgb = image.convert(Mode::RGB).ok()?;
    let small = rgb.resize(SIDE, SIDE, Filter::Bilinear, None, None).ok()?;
    let (indices, palette) = small.quantize(COLOURS).ok()?;
    let pixels: Vec<u8> = indices
        .iter()
        .flat_map(|&i| palette[usize::from(i)])
        .collect();
    let few = Image::from_bytes(Mode::RGB, SIDE, SIDE, pixels).ok()?;
    let (mut best, mut score) = (None, 0.0f64);
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
        if value > score {
            best = Some([r, g, b]);
            score = value;
        }
    }
    let mut best = best?;
    let top = best.into_iter().max().unwrap_or(0);
    if top < BRIGHT {
        let k = f64::from(BRIGHT) / f64::from(top);
        best = best.map(|c| (f64::from(c) * k).trunc().min(255.0) as u8);
    }
    Some(best)
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
        assert_eq!(first, find(&red));
        assert_eq!(dominant(&red.clone()), first); // (a copy: the same bytes, kept)
                                                   // (another picture of the same size, which a new track's cover may be, at once)
        assert_eq!(dominant(&blue), find(&blue));
        assert_ne!(dominant(&blue), first);
        assert_eq!(dominant(&picture(128, 128, 128)), None); // (greys carry none)
        for k in 0..KEPT as u8 {
            dominant(&picture(100 + k, 20, 20));
        }
        assert_eq!(dominant(&red), first); // (made anew once it was left out)
    }
}
