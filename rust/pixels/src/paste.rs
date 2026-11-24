//! Image.paste: an image or a colour into this one, through a mask or none
//! (libImaging/Paste.c's paste, paste_mask_L/RGBA/RGBa, fill, fill_mask_L/RGBA; PIL/Image.py's
//! way of choosing what is pasted).

use crate::ints::{blend, preblend, unmultiplied};
use crate::{error, Image, Mode, Result};

/// The part of a box that lies on an image of ``w`` by ``h``: where it goes (dx, dy), where it
/// comes from in the box (sx, sy), how much of it (xsize, ysize). None where nothing does.
fn clipped(w: usize, h: usize, x0: i64, y0: i64, x1: i64, y1: i64) -> Option<[usize; 6]> {
    let (mut xsize, mut ysize) = (x1 - x0, y1 - y0);
    let (mut dx, mut dy, mut sx, mut sy) = (x0, y0, 0, 0);
    if dx < 0 {
        xsize += dx;
        sx = -dx;
        dx = 0;
    }
    if dx + xsize > w as i64 {
        xsize = w as i64 - dx;
    }
    if dy < 0 {
        ysize += dy;
        sy = -dy;
        dy = 0;
    }
    if dy + ysize > h as i64 {
        ysize = h as i64 - dy;
    }
    if xsize <= 0 || ysize <= 0 {
        return None;
    }
    Some([
        dx as usize,
        dy as usize,
        sx as usize,
        sy as usize,
        xsize as usize,
        ysize as usize,
    ])
}

/// A mask's weight at a pixel and how it mixes (Paste.c's three kinds of matte).
#[derive(Clone, Copy)]
enum Matte {
    /// L: the mask's value mixes (BLEND)
    Plain,
    /// RGBA: its alpha mixes (BLEND)
    Alpha,
    /// RGBa: its alpha, the pasted colours premultiplied (PREBLEND)
    Premultiplied,
}

fn matte_of(mask: &Image) -> Result<Matte> {
    match mask.mode {
        Mode::L => Ok(Matte::Plain),
        Mode::RGBA => Ok(Matte::Alpha),
        Mode::RGBa => Ok(Matte::Premultiplied),
        Mode::RGB => error("bad transparency mask"),
    }
}

#[inline(always)]
fn weight(mask: &Image, matte: Matte, i: usize) -> u32 {
    match matte {
        Matte::Plain => u32::from(mask.data[i]),
        Matte::Alpha | Matte::Premultiplied => u32::from(mask.data[i * 4 + 3]),
    }
}

/// One pixel `from` onto `out` through the weight `a`: PREBLEND for a premultiplied matte, else
/// BLEND (which keeps what is there through 0 and takes what comes through 255, exactly).
#[inline(always)]
fn mix(out: &mut [u8], from: &[u8], a: u32, pre: bool) {
    if pre {
        for (out, &i) in out.iter_mut().zip(from) {
            *out = preblend(a, u32::from(*out), u32::from(i));
        }
    } else if a == 255 {
        let n = out.len();
        out.copy_from_slice(&from[..n]);
    } else if a != 0 {
        for (out, &i) in out.iter_mut().zip(from) {
            *out = blend(a, u32::from(*out), u32::from(i));
        }
    }
}

/// A row of `DB`-band pixels, the pasted ones of `SB` bands, through `weights`.
#[inline(always)]
fn mix_row<const DB: usize, const SB: usize>(
    to: &mut [u8],
    from: &[u8],
    weights: impl Iterator<Item = u32>,
    pre: bool,
) {
    for ((out, i), a) in to
        .chunks_exact_mut(DB)
        .zip(from.chunks_exact(SB))
        .zip(weights)
    {
        mix(out, i, a, pre);
    }
}

/// The weights of an L mask's `n` pixels from pixel `s` on.
fn mask_l(mask: &Image, s: usize, n: usize) -> impl Iterator<Item = u32> + '_ {
    mask.data[s..s + n].iter().map(|&v| u32::from(v))
}

/// The weights of an RGBA or RGBa mask's `n` pixels from pixel `s` on: their alpha.
fn mask_a(mask: &Image, s: usize, n: usize) -> impl Iterator<Item = u32> + '_ {
    mask.data[s * 4..(s + n) * 4]
        .chunks_exact(4)
        .map(|p| u32::from(p[3]))
}

impl Image {
    /// Image.paste(im, (x, y), mask): ``im`` with its corner at (x, y); through ``mask`` (of
    /// im's size: L, or the alpha of RGBA or RGBa) where one is given. Image.py converts ``im``
    /// to this image's mode first, but for RGBA and RGBa into RGB, whose colours Paste.c takes
    /// as they are.
    pub fn paste(&mut self, im: &Image, x: i64, y: i64, mask: Option<&Image>) -> Result<()> {
        let converted;
        let im = if im.mode == self.mode
            || (self.mode == Mode::RGB && matches!(im.mode, Mode::RGBA | Mode::RGBa))
        {
            im
        } else {
            converted = im.convert(self.mode)?;
            &converted
        };
        if mask.is_some_and(|m| m.size() != im.size()) {
            return error("images do not match");
        }
        let Some([dx, dy, sx, sy, xsize, ysize]) = clipped(
            self.width,
            self.height,
            x,
            y,
            x + im.width as i64,
            y + im.height as i64,
        ) else {
            return Ok(()); // (Paste.c looks at the mask's mode only where something is pasted)
        };
        let matte = mask.map(matte_of).transpose()?;
        let (db, sb) = (self.bands(), im.bands());
        let (Some(matte), Some(mask)) = (matte, mask) else {
            for row in 0..ysize {
                let s = ((sy + row) * im.width + sx) * sb;
                let d = ((dy + row) * self.width + dx) * db;
                if sb == db {
                    self.data[d..d + xsize * db].copy_from_slice(&im.data[s..s + xsize * sb]);
                } else {
                    for col in 0..xsize {
                        let (d, s) = (d + col * db, s + col * sb);
                        self.data[d..d + db].copy_from_slice(&im.data[s..s + db]);
                    }
                }
            }
            return Ok(());
        };
        if self.mode == Mode::RGB && im.mode == Mode::RGBA && std::ptr::eq(mask, im) {
            // (an RGBA picture through its own alpha: four pixels at a time where the
            // processor can, the same bytes)
            return self.paste_part(im, (0, 0, im.width, im.height), x, y);
        }
        for row in 0..ysize {
            let s = (sy + row) * im.width + sx;
            let d = ((dy + row) * self.width + dx) * db;
            let to = &mut self.data[d..d + xsize * db];
            let from = &im.data[s * sb..(s + xsize) * sb];
            let pre = matches!(matte, Matte::Premultiplied);
            // (each row in one go, its bands a pixel known: the same sums as pixel by pixel)
            match (matte, db, sb) {
                (Matte::Plain, 3, 3) => mix_row::<3, 3>(to, from, mask_l(mask, s, xsize), pre),
                (Matte::Plain, 4, 4) => mix_row::<4, 4>(to, from, mask_l(mask, s, xsize), pre),
                (Matte::Plain, 1, 1) => mix_row::<1, 1>(to, from, mask_l(mask, s, xsize), pre),
                (Matte::Alpha | Matte::Premultiplied, 3, 3) => {
                    mix_row::<3, 3>(to, from, mask_a(mask, s, xsize), pre)
                }
                (Matte::Alpha | Matte::Premultiplied, 3, 4) => {
                    mix_row::<3, 4>(to, from, mask_a(mask, s, xsize), pre)
                }
                (Matte::Alpha | Matte::Premultiplied, 4, 4) => {
                    mix_row::<4, 4>(to, from, mask_a(mask, s, xsize), pre)
                }
                _ => {
                    for col in 0..xsize {
                        let a = weight(mask, matte, s + col);
                        let out = &mut to[col * db..col * db + db];
                        let from = &from[col * sb..col * sb + sb];
                        mix(out, from, a, pre);
                    }
                }
            }
        }
        Ok(())
    }

    /// An RGBa picture laid onto this RGB one through its own alpha, each alpha first taken
    /// through `fade` where given: what `s = im.convert("RGBA")`, its alpha through
    /// `point(fade)` (`putalpha`), then `paste(s, (x, y), s)` give, in one pass and without the
    /// pictures between.
    pub fn paste_unpremultiplied(
        &mut self,
        im: &Image,
        x: i64,
        y: i64,
        fade: Option<&[u8; 256]>,
    ) -> Result<()> {
        if self.mode != Mode::RGB || im.mode != Mode::RGBa {
            return error("an RGBa picture onto an RGB one only");
        }
        let Some([dx, dy, sx, sy, xsize, ysize]) = clipped(
            self.width,
            self.height,
            x,
            y,
            x + im.width as i64,
            y + im.height as i64,
        ) else {
            return Ok(());
        };
        for row in 0..ysize {
            let s = ((sy + row) * im.width + sx) * 4;
            let d = ((dy + row) * self.width + dx) * 3;
            let to = &mut self.data[d..d + xsize * 3];
            let from = &im.data[s..s + xsize * 4];
            for (out, p) in to.chunks_exact_mut(3).zip(from.chunks_exact(4)) {
                let alpha = p[3];
                let a = fade.map_or(alpha, |f| f[usize::from(alpha)]);
                if a == 0 {
                    continue;
                }
                // (of alpha 255 the colours are plain already: rgba2rgbA leaves them)
                let plain = if alpha == 255 {
                    [p[0], p[1], p[2]]
                } else {
                    [
                        unmultiplied(p[0], alpha),
                        unmultiplied(p[1], alpha),
                        unmultiplied(p[2], alpha),
                    ]
                };
                if a == 255 {
                    out.copy_from_slice(&plain);
                } else {
                    let a = u32::from(a);
                    for (o, &c) in out.iter_mut().zip(&plain) {
                        *o = blend(a, u32::from(*o), u32::from(c));
                    }
                }
            }
        }
        Ok(())
    }

    /// Image.paste(im.crop(part), (x, y), im.crop(part)): the box `part` (left, top, right,
    /// bottom, inside it) of `im` (RGBA or RGBa, into RGB, RGBA or RGBa), through its own alpha,
    /// with its corner at (x, y); without cutting the part out first.
    pub fn paste_part(
        &mut self,
        im: &Image,
        part: (usize, usize, usize, usize),
        x: i64,
        y: i64,
    ) -> Result<()> {
        let (px0, py0, px1, py1) = part;
        if !im.mode.has_alpha() || px1 > im.width || py1 > im.height || px0 > px1 || py0 > py1 {
            return error("bad part to paste");
        }
        if self.mode == Mode::L || (self.mode.has_alpha() && self.mode != im.mode) {
            // (what converts first: as paste does it)
            let cut = im.crop(px0 as i64, py0 as i64, px1 as i64, py1 as i64);
            return self.paste(&cut, x, y, Some(&cut));
        }
        let (w, h) = ((px1 - px0) as i64, (py1 - py0) as i64);
        let Some([dx, dy, sx, sy, xsize, ysize]) =
            clipped(self.width, self.height, x, y, x + w, y + h)
        else {
            return Ok(());
        };
        let premultiplied = im.mode == Mode::RGBa;
        let db = self.bands();
        let wide = db == 3 && !premultiplied && rows::ready();
        for row in 0..ysize {
            let s = ((py0 + sy + row) * im.width + px0 + sx) * 4;
            let d = ((dy + row) * self.width + dx) * db;
            let from = &im.data[s..s + xsize * 4];
            // (four pixels at a time where the processor can: the same bytes)
            let done = if wide {
                rows::blend_onto_rgb(&mut self.data[d..], from, xsize)
            } else {
                0
            };
            let to = &mut self.data[d + done * db..d + xsize * db];
            let from = &from[done * 4..];
            for (out, i) in to.chunks_exact_mut(db).zip(from.chunks_exact(4)) {
                let a = u32::from(i[3]);
                if premultiplied {
                    for (o, &c) in out.iter_mut().zip(i) {
                        *o = preblend(a, u32::from(*o), u32::from(c));
                    }
                } else if a == 255 {
                    out.copy_from_slice(&i[..db]);
                } else if a != 0 {
                    for (o, &c) in out.iter_mut().zip(i) {
                        *o = blend(a, u32::from(*o), u32::from(c));
                    }
                }
            }
        }
        Ok(())
    }

    /// Image.paste(colour, (x0, y0, x1, y1), mask): the box filled with ``colour`` (one value a
    /// band), through ``mask`` (of the box's size) where one is given (ImagingFill2).
    pub fn paste_colour(
        &mut self,
        colour: &[u8],
        bounds: (i64, i64, i64, i64),
        mask: Option<&Image>,
    ) -> Result<()> {
        let (x0, y0, x1, y1) = bounds;
        if colour.len() != self.bands() {
            return error("wrong number of bands in the colour");
        }
        if let Some(m) = mask {
            if (m.width as i64, m.height as i64) != (x1 - x0, y1 - y0) {
                return error("images do not match");
            }
        }
        let Some([dx, dy, sx, sy, xsize, ysize]) = clipped(self.width, self.height, x0, y0, x1, y1)
        else {
            return Ok(());
        };
        if let Some(m) = mask {
            if !matches!(m.mode, Mode::L | Mode::RGBA) {
                return error(format!("a fill through a mask of {}", m.mode));
            }
        }
        let db = self.bands();
        let alpha_channel = self.mode.has_alpha();
        for row in 0..ysize {
            for col in 0..xsize {
                let d = ((dy + row) * self.width + dx + col) * db;
                let Some(mask) = mask else {
                    self.data[d..d + db].copy_from_slice(colour);
                    continue;
                };
                let s = (sy + row) * mask.width + sx + col;
                match mask.mode {
                    Mode::L => {
                        // fill_mask_L: where this image is clear, its colours take the ink
                        // whole wherever the mask has any of it (the alpha mixes as given)
                        let m = mask.data[s];
                        let clear = self.data[d + db - 1] == 0;
                        let px = &mut self.data[d..d + db];
                        for (b, (out, &ink)) in px.iter_mut().zip(colour).enumerate() {
                            let mut w = u32::from(m);
                            if alpha_channel && b != 3 && m != 0 && clear {
                                w = 255;
                            }
                            *out = blend(w, u32::from(*out), u32::from(ink));
                        }
                    }
                    Mode::RGBA => {
                        let w = u32::from(mask.data[s * 4 + 3]);
                        for (out, &ink) in self.data[d..d + db].iter_mut().zip(colour) {
                            *out = blend(w, u32::from(*out), u32::from(ink));
                        }
                    }
                    other => return error(format!("a fill through a mask of {other}")),
                }
            }
        }
        Ok(())
    }
}

/// BLEND of RGBA pixels through their own alpha onto RGB ones, four at a time (SSSE3).
mod rows {
    /// Whether the processor has what blend_onto_rgb takes.
    pub fn ready() -> bool {
        #[cfg(target_arch = "x86_64")]
        {
            static READY: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
            *READY.get_or_init(|| std::arch::is_x86_feature_detected!("ssse3"))
        }
        #[cfg(not(target_arch = "x86_64"))]
        {
            false
        }
    }

    /// The first pixels of `from` (RGBA) blended onto those of `to` (RGB, the row from there
    /// on), `n` of them at most, as Paste.c's BLEND has each band: how many it did (in fours,
    /// while the row goes on 4 bytes past them; the rest are the caller's).
    pub fn blend_onto_rgb(to: &mut [u8], from: &[u8], n: usize) -> usize {
        #[cfg(target_arch = "x86_64")]
        {
            if ready() {
                // SAFETY: the processor has SSSE3 (ready); the loads and stores stay within
                // the slices (checked in the loop).
                return unsafe { blend_ssse3(to, from, n) };
            }
        }
        let _ = (to, from, n);
        0
    }

    #[cfg(target_arch = "x86_64")]
    #[target_feature(enable = "ssse3")]
    unsafe fn blend_ssse3(to: &mut [u8], from: &[u8], n: usize) -> usize {
        use std::arch::x86_64::*;
        let dst_wide = _mm_setr_epi8(0, 1, 2, -1, 3, 4, 5, -1, 6, 7, 8, -1, 9, 10, 11, -1);
        let src_alpha = _mm_setr_epi8(3, 3, 3, -1, 7, 7, 7, -1, 11, 11, 11, -1, 15, 15, 15, -1);
        let src_rgb = _mm_setr_epi8(0, 1, 2, -1, 4, 5, 6, -1, 8, 9, 10, -1, 12, 13, 14, -1);
        let narrow = _mm_setr_epi8(0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 14, -1, -1, -1, -1);
        let keep = _mm_setr_epi8(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -1, -1, -1, -1);
        let zero = _mm_setzero_si128();
        let full = _mm_set1_epi16(255);
        let half = _mm_set1_epi16(128);
        let mut done = 0;
        while done + 4 <= n && done * 3 + 16 <= to.len() && done * 4 + 16 <= from.len() {
            let s = _mm_loadu_si128(from.as_ptr().add(done * 4) as *const __m128i);
            let d = _mm_loadu_si128(to.as_ptr().add(done * 3) as *const __m128i);
            let dd = _mm_shuffle_epi8(d, dst_wide);
            let aa = _mm_shuffle_epi8(s, src_alpha);
            let ss = _mm_shuffle_epi8(s, src_rgb);
            let mix = |d: __m128i, s: __m128i, a: __m128i| {
                // in1 * (255 - a) + in2 * a, then DIV255 ((t + 128) + ((t + 128) >> 8)) >> 8:
                // at most 65025 + 128 + 254, within 16 bits
                let t = _mm_add_epi16(
                    _mm_mullo_epi16(d, _mm_sub_epi16(full, a)),
                    _mm_mullo_epi16(s, a),
                );
                let t = _mm_add_epi16(t, half);
                _mm_srli_epi16(_mm_add_epi16(t, _mm_srli_epi16(t, 8)), 8)
            };
            let low = mix(
                _mm_unpacklo_epi8(dd, zero),
                _mm_unpacklo_epi8(ss, zero),
                _mm_unpacklo_epi8(aa, zero),
            );
            let high = mix(
                _mm_unpackhi_epi8(dd, zero),
                _mm_unpackhi_epi8(ss, zero),
                _mm_unpackhi_epi8(aa, zero),
            );
            let out = _mm_shuffle_epi8(_mm_packus_epi16(low, high), narrow);
            let out = _mm_or_si128(out, _mm_and_si128(d, keep));
            _mm_storeu_si128(to.as_mut_ptr().add(done * 3) as *mut __m128i, out);
            done += 4;
        }
        done
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rgba_through_its_alpha_onto_rgb_four_at_a_time_as_one_by_one() {
        // (every alpha, colours anywhere, rows of every length and offsets)
        let mut seed = 0x1234_5678_9abc_def0_u64;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            (seed >> 16) as u8
        };
        for (w, h) in [
            (1usize, 1usize),
            (3, 2),
            (4, 1),
            (5, 3),
            (17, 4),
            (64, 9),
            (257, 3),
        ] {
            let layer: Vec<u8> = (0..w * h * 4).map(|_| next()).collect();
            let mut layer = Image::from_bytes(Mode::RGBA, w, h, layer).unwrap();
            for (i, p) in layer.data.chunks_exact_mut(4).enumerate() {
                p[3] = match i % 5 {
                    0 => 0,
                    1 => 255,
                    _ => p[3],
                };
            }
            let ground: Vec<u8> = (0..(w + 7) * (h + 2) * 3).map(|_| next()).collect();
            let ground = Image::from_bytes(Mode::RGB, w + 7, h + 2, ground).unwrap();
            for (x, y) in [(0i64, 0i64), (3, 1), (7, 2), (-2, 0)] {
                let mut fast = ground.clone();
                fast.paste_part(&layer, (0, 0, w, h), x, y).unwrap();
                let mut slow = ground.clone();
                let cut = layer.crop(0, 0, w as i64, h as i64);
                slow.paste(&cut, x, y, Some(&cut)).unwrap();
                assert_eq!(fast, slow, "{w}x{h} at {x},{y}");
            }
        }
    }

    #[test]
    fn of_alpha_255_the_colours_are_plain_already() {
        for c in 0..=255u8 {
            assert_eq!(unmultiplied(c, 255), c);
        }
    }

    #[test]
    fn rgba_laid_in_one_pass_as_converted_faded_and_pasted() {
        // (premultiplied pictures of every alpha, with and without a fade, anywhere on the
        // picture and beyond it)
        let mut seed = 0x0bad_cafe_1234_5678_u64;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            (seed >> 16) as u8
        };
        for (w, h) in [(1usize, 1usize), (7, 3), (40, 17), (130, 9)] {
            let mut plain = Image::blank(Mode::RGBA, w, h);
            for v in plain.data.iter_mut() {
                *v = next();
            }
            let im = plain.convert(Mode::RGBa).unwrap();
            let mut ground = Image::blank(Mode::RGB, 60, 20);
            for v in ground.data.iter_mut() {
                *v = next();
            }
            for fade in [None, Some(0.0), Some(0.37), Some(0.998)] {
                let lut: Option<[u8; 256]> = fade
                    .map(|f: f64| std::array::from_fn(|v| (v as f64 * f).round_ties_even() as u8));
                for (x, y) in [(0i64, 0i64), (-3, 2), (25, -4), (55, 15), (70, 0)] {
                    let mut fast = ground.clone();
                    fast.paste_unpremultiplied(&im, x, y, lut.as_ref()).unwrap();
                    let mut s = im.convert(Mode::RGBA).unwrap();
                    if let Some(lut) = &lut {
                        let table: Vec<i64> = lut.iter().map(|&v| i64::from(v)).collect();
                        let a = s.getchannel(3).unwrap().point(&table).unwrap();
                        s.putalpha(&a).unwrap();
                    }
                    let mut slow = ground.clone();
                    slow.paste(&s, x, y, Some(&s)).unwrap();
                    assert_eq!(fast, slow, "{w}x{h} at {x},{y}, {fade:?}");
                }
            }
        }
    }
}
