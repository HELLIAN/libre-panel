//! The parts of a frame that changed since the one before (render/renderer.py's changed_region
//! and changed_regions), for a panel that takes a part of its screen: one box around all of
//! them, or a few boxes for a panel on a slow link.

use lp_pixels::{Image, Mode};

use crate::driver::Region;

/// changed_region: the box of the pixels that differ, None where none does; the whole frame
/// where there is no frame before of its size and mode.
pub fn changed_region(previous: Option<&Image>, current: &Image) -> Option<Region> {
    let whole = (0, 0, current.width() as i64, current.height() as i64);
    let Some(previous) = previous else {
        return Some(whole);
    };
    if previous.size() != current.size() || previous.mode() != current.mode() {
        return Some(whole);
    }
    if matches!(current.mode(), Mode::RGB | Mode::L) {
        return differing(previous, current);
    }
    let diff = lp_pixels::chops::difference(previous, current).ok()?;
    diff.getbbox(true)
        .map(|(x0, y0, x1, y1)| (x0 as i64, y0 as i64, x1 as i64, y1 as i64))
}

/// changed_region of two frames of one size and mode whose pixels differ where their bytes do
/// (RGB, L: where their difference is not black), without making the difference: the rows
/// from the top and from the bottom compared whole, then the columns of the rows between from
/// either side, each only as far as the box is not yet known to reach.
fn differing(previous: &Image, current: &Image) -> Option<Region> {
    let (w, h) = (current.width(), current.height());
    let (before, now) = (previous.as_bytes(), current.as_bytes());
    if w == 0 || h == 0 || before.len() != now.len() || now.len() % (w * h) != 0 {
        return None;
    }
    let size = now.len() / (w * h); // (bytes a pixel)
    let line = w * size;
    let rows = |y: usize| {
        (
            &before[y * line..(y + 1) * line],
            &now[y * line..(y + 1) * line],
        )
    };
    let differs = |y: usize| {
        let (a, b) = rows(y);
        a != b
    };
    let top = (0..h).find(|&y| differs(y))?;
    let bottom = (top..h).rev().find(|&y| differs(y)).map_or(h, |y| y + 1);
    let (mut left, mut right) = (w, 0);
    for y in top..bottom {
        let (a, b) = rows(y);
        if let Some(at) = first_difference(&a[..left * size], &b[..left * size]) {
            left = at / size;
        }
        let from = right.max(left) * size;
        if let Some(at) = last_difference(&a[from..], &b[from..]) {
            right = (from + at) / size + 1;
        }
    }
    Some((left as i64, top as i64, right as i64, bottom as i64))
}

/// How far the bytes compared at once go (whole blocks compare as memory does).
const BLOCK: usize = 64;

/// The place of the first byte of `a` that differs from `b`'s.
fn first_difference(a: &[u8], b: &[u8]) -> Option<usize> {
    a.chunks(BLOCK)
        .zip(b.chunks(BLOCK))
        .enumerate()
        .find(|(_, (x, y))| x != y)
        .and_then(|(n, (x, y))| {
            let at = x.iter().zip(y.iter()).position(|(p, q)| p != q)?;
            Some(n * BLOCK + at)
        })
}

/// The place of the last byte of `a` that differs from `b`'s.
fn last_difference(a: &[u8], b: &[u8]) -> Option<usize> {
    a.rchunks(BLOCK)
        .zip(b.rchunks(BLOCK))
        .enumerate()
        .find(|(_, (x, y))| x != y)
        .and_then(|(n, (x, y))| {
            let at = x.iter().zip(y.iter()).rposition(|(p, q)| p != q)?;
            Some(a.len().saturating_sub((n + 1) * BLOCK) + at)
        })
}

/// The runs of set flags, those closer than `gap` joined: (first, after the last).
fn runs(flags: &[bool], gap: usize) -> Vec<(usize, usize)> {
    let mut found: Vec<(usize, usize)> = Vec::new();
    for (i, _) in flags.iter().enumerate().filter(|(_, on)| **on) {
        match found.last_mut() {
            Some(last) if i - last.1 <= gap => last.1 = i + 1,
            _ => found.push((i, i + 1)),
        }
    }
    found
}

fn joined(a: Region, b: Region) -> Region {
    (a.0.min(b.0), a.1.min(b.1), a.2.max(b.2), a.3.max(b.3))
}

fn area(b: Region) -> i64 {
    (b.2 - b.0) * (b.3 - b.1)
}

/// changed_regions: the parts that changed as a few boxes, for a panel on a slow link. Rows of
/// changes closer than `gap` px are one band, and so are columns within a band; then the two
/// boxes whose joined box adds the fewest pixels are joined until `most` are left.
pub fn changed_regions(
    previous: Option<&Image>,
    current: &Image,
    gap: usize,
    most: usize,
) -> Vec<Region> {
    let Some(whole) = changed_region(previous, current) else {
        return Vec::new();
    };
    let Some(previous) = previous else {
        return vec![whole];
    };
    if previous.size() != current.size() || previous.mode() != current.mode() {
        return vec![whole];
    }
    let (x0, y0, x1, y1) = whole;
    let diff = lp_pixels::chops::difference(
        &previous.crop(x0, y0, x1, y1),
        &current.crop(x0, y0, x1, y1),
    );
    let grey = match diff.and_then(|diff| {
        if diff.mode() == Mode::L {
            Ok(diff)
        } else {
            diff.convert(Mode::L)
        }
    }) {
        Ok(grey) => grey,
        Err(_) => return vec![whole],
    };
    let (w, h) = (grey.width(), grey.height());
    let changed = grey.as_bytes();
    let rows: Vec<bool> = (0..h)
        .map(|y| changed[y * w..(y + 1) * w].iter().any(|v| *v > 0))
        .collect();
    let mut boxes: Vec<Region> = Vec::new();
    for (top, bottom) in runs(&rows, gap) {
        let columns: Vec<bool> = (0..w)
            .map(|x| (top..bottom).any(|y| changed[y * w + x] > 0))
            .collect();
        for (left, right) in runs(&columns, gap) {
            boxes.push((
                x0 + left as i64,
                y0 + top as i64,
                x0 + right as i64,
                y0 + bottom as i64,
            ));
        }
    }
    if boxes.len() > 8 * most {
        return vec![whole];
    }
    while boxes.len() > most {
        let mut best: Option<(i64, usize, usize)> = None;
        for i in 0..boxes.len() {
            for j in i + 1..boxes.len() {
                let cost = area(joined(boxes[i], boxes[j])) - area(boxes[i]) - area(boxes[j]);
                if best.is_none_or(|(least, ..)| cost < least) {
                    best = Some((cost, i, j));
                }
            }
        }
        let (_, i, j) = best.expect("two boxes at least");
        boxes[i] = joined(boxes[i], boxes[j]);
        boxes.remove(j);
    }
    boxes
}

#[cfg(test)]
mod tests {
    use super::*;

    /// changed_region as it was made: the difference and its box (Pillow's way).
    fn made(previous: &Image, current: &Image) -> Option<Region> {
        let diff = lp_pixels::chops::difference(previous, current).ok()?;
        diff.getbbox(true)
            .map(|(x0, y0, x1, y1)| (x0 as i64, y0 as i64, x1 as i64, y1 as i64))
    }

    #[test]
    fn the_box_of_what_changed_is_the_difference_s_box() {
        let mut seed = 7u64;
        let mut next = |n: usize| {
            seed = seed
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            (seed >> 33) as usize % n
        };
        for mode in [Mode::RGB, Mode::L] {
            for _ in 0..2000 {
                let (w, h) = (1 + next(90), 1 + next(30));
                let colour = vec![next(256) as u8; if mode == Mode::RGB { 3 } else { 1 }];
                let before = Image::new(mode, w, h, &colour);
                let mut after = before.clone();
                let size = after.as_bytes().len() / (w * h);
                for _ in 0..next(5) {
                    // (a byte of one pixel's: one band of it may change alone)
                    let (x, y, band) = (next(w), next(h), next(size));
                    let at = (y * w + x) * size + band;
                    let bytes = after.as_bytes_mut();
                    bytes[at] = bytes[at].wrapping_add(1 + next(255) as u8);
                }
                assert_eq!(
                    differing(&before, &after),
                    made(&before, &after),
                    "{mode:?} {w}x{h}"
                );
                assert_eq!(changed_region(Some(&before), &after), made(&before, &after));
            }
        }
    }

    #[test]
    fn a_frame_with_none_before_or_of_another_size_is_whole() {
        let frame = Image::new(Mode::RGB, 4, 3, &[1, 2, 3]);
        assert_eq!(changed_region(None, &frame), Some((0, 0, 4, 3)));
        let other = Image::new(Mode::RGB, 3, 3, &[1, 2, 3]);
        assert_eq!(changed_region(Some(&other), &frame), Some((0, 0, 4, 3)));
        assert_eq!(changed_region(Some(&frame), &frame), None);
    }
}
