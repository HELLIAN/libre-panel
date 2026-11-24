//! Image.resize(size, resample, box, reducing_gap): libImaging/Resample.c's two passes (the
//! rows first, rounded to 8 bits, then the columns), Geometry.c's nearest-neighbour scaling
//! for NEAREST, _imaging.c's crop where the box is the size asked for, and PIL/Image.py's way
//! round (RGBA premultiplied, reduce first by ``reducing_gap``, a very tall picture in two
//! steps).
//!
//! The arithmetic is Resample.c's: the box's corners are floats (_imaging.c parses them so),
//! the filter's weights doubles, normalised and made 22-bit fixed point with C's truncation.

use crate::{error, FloatImage, Image, Mode, Result};
use std::f64::consts::PI;

/// Pillow's resampling filters (Image.Resampling).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Filter {
    Nearest,
    Box,
    Bilinear,
    Hamming,
    Bicubic,
    Lanczos,
}

impl Filter {
    /// Image.Resampling's numbers.
    pub fn from_number(n: i64) -> Option<Filter> {
        Some(match n {
            0 => Filter::Nearest,
            1 => Filter::Lanczos,
            2 => Filter::Bilinear,
            3 => Filter::Bicubic,
            4 => Filter::Box,
            5 => Filter::Hamming,
            _ => return None,
        })
    }

    /// The filter's support (Resample.c's, and Image.py's _filters_support).
    fn support(self) -> f64 {
        match self {
            Filter::Nearest | Filter::Box => 0.5,
            Filter::Bilinear | Filter::Hamming => 1.0,
            Filter::Bicubic => 2.0,
            Filter::Lanczos => 3.0,
        }
    }

    fn weight(self, x: f64) -> f64 {
        match self {
            Filter::Nearest | Filter::Box => {
                if x > -0.5 && x <= 0.5 {
                    1.0
                } else {
                    0.0
                }
            }
            Filter::Bilinear => {
                let x = x.abs();
                if x < 1.0 {
                    1.0 - x
                } else {
                    0.0
                }
            }
            Filter::Hamming => {
                let x = x.abs();
                if x == 0.0 {
                    return 1.0;
                }
                if x >= 1.0 {
                    return 0.0;
                }
                let x = x * PI;
                // (0.54f and 0.46f: float constants in Resample.c)
                x.sin() / x * (f64::from(0.54f32) + f64::from(0.46f32) * x.cos())
            }
            Filter::Bicubic => {
                let a = -0.5;
                let x = x.abs();
                if x < 1.0 {
                    ((a + 2.0) * x - (a + 3.0)) * x * x + 1.0
                } else if x < 2.0 {
                    (((x - 5.0) * x + 8.0) * x - 4.0) * a
                } else {
                    0.0
                }
            }
            Filter::Lanczos => {
                if (-3.0..3.0).contains(&x) {
                    sinc(x) * sinc(x / 3.0)
                } else {
                    0.0
                }
            }
        }
    }
}

fn sinc(x: f64) -> f64 {
    if x == 0.0 {
        return 1.0;
    }
    let x = x * PI;
    x.sin() / x
}

const PRECISION_BITS: u32 = 32 - 8 - 2;

/// Resample.c's precompute_coeffs: for each place of the output, the first input it takes and
/// how many, and their weights (``ksize`` a place).
struct Coeffs {
    ksize: usize,
    bounds: Vec<(usize, usize)>,
    kk: Vec<f64>,
}

fn coeffs(in_size: usize, in0: f32, in1: f32, out_size: usize, filter: Filter) -> Coeffs {
    let scale = f64::from(in1 - in0) / out_size as f64;
    let filterscale = scale.max(1.0);
    let support = filter.support() * filterscale;
    let ksize = support.ceil() as usize * 2 + 1;
    let mut kk = vec![0.0; out_size * ksize];
    let mut bounds = Vec::with_capacity(out_size);
    let inv_filterscale = 1.0 / filterscale;
    for xx in 0..out_size {
        let center = f64::from(in0) + (xx as f64 + 0.5) * scale;
        let mut ww = 0.0;
        let xmin = ((center - support + 0.5) as i32).max(0);
        let xmax = ((center + support + 0.5) as i32).min(in_size as i32) - xmin;
        let k = &mut kk[xx * ksize..(xx + 1) * ksize];
        for x in 0..xmax.max(0) {
            let w = filter.weight((f64::from(x + xmin) - center + 0.5) * inv_filterscale);
            k[x as usize] = w;
            ww += w;
        }
        if ww != 0.0 {
            for v in &mut k[..xmax.max(0) as usize] {
                *v /= ww;
            }
        }
        bounds.push((xmin as usize, xmax.max(0) as usize));
    }
    Coeffs { ksize, bounds, kk }
}

/// normalize_coeffs_8bpc: the weights in 22-bit fixed point, rounded away from 0 by C's cast.
fn fixed(kk: &[f64]) -> Vec<i32> {
    let one = f64::from(1u32 << PRECISION_BITS);
    kk.iter()
        .map(|&k| {
            if k < 0.0 {
                (-0.5 + k * one) as i32
            } else {
                (0.5 + k * one) as i32
            }
        })
        .collect()
}

/// clip8: the fixed-point sum to 0..255 (Resample.c's table of -640..639 >> 22).
#[inline(always)]
fn clip8(ss: i32) -> u8 {
    (ss >> PRECISION_BITS).clamp(0, 255) as u8
}

/// One row resampled to ``c``'s width, `B` bands a pixel: each pixel's bands summed together
/// over its weights (in 32 bits, wrapping, as Resample.c's int sums: the order adds nothing).
#[inline(always)]
fn horizontal_row<const B: usize>(line: &[u8], dst: &mut [u8], c: &Coeffs, kk: &[i32]) {
    for (xx, &(xmin, xmax)) in c.bounds.iter().enumerate() {
        let k = &kk[xx * c.ksize..xx * c.ksize + xmax];
        let px = &line[xmin * B..(xmin + xmax) * B];
        let mut ss = [1i32 << (PRECISION_BITS - 1); B];
        for (p, &kx) in px.chunks_exact(B).zip(k) {
            for band in 0..B {
                ss[band] = ss[band].wrapping_add(i32::from(p[band]).wrapping_mul(kx));
            }
        }
        for band in 0..B {
            dst[xx * B + band] = clip8(ss[band]);
        }
    }
}

/// The rows of ``src`` from ``offset`` on, each resampled to ``c``'s width (8 bits a band).
#[inline(always)]
fn horizontal_8(src: &Image, offset: usize, rows: usize, c: &Coeffs) -> Image {
    let w = c.bounds.len();
    let kk = fixed(&c.kk);
    let mut out = Image::blank(src.mode, w, rows);
    for yy in 0..rows {
        let line = src.row(yy + offset);
        let dst = out.row_mut(yy);
        match src.bands() {
            1 => horizontal_row::<1>(line, dst, c, &kk),
            3 => horizontal_row::<3>(line, dst, c, &kk),
            _ => horizontal_row::<4>(line, dst, c, &kk),
        }
    }
    out
}

/// The columns of ``src``, resampled to ``c``'s height (``c``'s bounds shifted by ``offset``):
/// each output row summed a whole input row at a time (the same int sums as Resample.c's,
/// column by column).
#[inline(always)]
fn vertical_8(src: &Image, offset: usize, c: &Coeffs) -> Image {
    let h = c.bounds.len();
    let kk = fixed(&c.kk);
    let mut out = Image::blank(src.mode, src.width, h);
    let n = src.width * src.bands();
    let mut acc = vec![0i32; n];
    for (yy, &(ymin, ymax)) in c.bounds.iter().enumerate() {
        let k = &kk[yy * c.ksize..yy * c.ksize + ymax];
        acc.fill(1 << (PRECISION_BITS - 1));
        for (y, &ky) in k.iter().enumerate() {
            let at = (y + ymin - offset) * n;
            for (a, &v) in acc.iter_mut().zip(&src.data[at..at + n]) {
                *a = a.wrapping_add(i32::from(v).wrapping_mul(ky));
            }
        }
        for (o, &a) in out.row_mut(yy).iter_mut().zip(&acc) {
            *o = clip8(a);
        }
    }
    out
}

/// Whether Resample.c resamples the rows first (Pillow 13: not where the height shrinks by more
/// than twice what the width does, which it resamples first to make the second pass cheaper).
fn horizontal_first(in_w: usize, in_h: usize, w: usize, h: usize) -> bool {
    let shrink_y = in_h as i64 - h as i64;
    !(shrink_y > 0 && shrink_y > (in_w as i64 - w as i64) * 2)
}

/// Resample.c's ImagingResampleInner for 8-bit bands: compiled for AVX2 as well and that one
/// taken where the processor has it (the same int sums, more of them at once).
fn resample_8(src: &Image, w: usize, h: usize, filter: Filter, bx: [f32; 4]) -> Image {
    #[cfg(target_arch = "x86_64")]
    {
        static READY: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
        if *READY.get_or_init(|| std::arch::is_x86_feature_detected!("avx2")) {
            // SAFETY: the processor has AVX2.
            return unsafe { resample_8_avx2(src, w, h, filter, bx) };
        }
    }
    resample_8_body(src, w, h, filter, bx)
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn resample_8_avx2(src: &Image, w: usize, h: usize, filter: Filter, bx: [f32; 4]) -> Image {
    resample_8_body(src, w, h, filter, bx)
}

#[inline(always)]
fn resample_8_body(src: &Image, w: usize, h: usize, filter: Filter, bx: [f32; 4]) -> Image {
    let need_h = w != src.width || bx[0] != 0.0 || bx[2] != w as f32;
    let need_v = h != src.height || bx[1] != 0.0 || bx[3] != h as f32;
    let cv = coeffs(src.height, bx[1], bx[3], h, filter);
    if !horizontal_first(src.width, src.height, w, h) {
        // (the height shrinks: need_v; the columns first, then the rows of what they made)
        let tall = vertical_8(src, 0, &cv);
        if need_h {
            let ch = coeffs(src.width, bx[0], bx[2], w, filter);
            return horizontal_8(&tall, 0, h, &ch);
        }
        return tall;
    }
    let first = cv.bounds[0].0;
    let last = cv.bounds[h - 1].0 + cv.bounds[h - 1].1;
    let mut current: Option<Image> = None;
    let mut offset = 0;
    if need_h {
        let ch = coeffs(src.width, bx[0], bx[2], w, filter);
        current = Some(horizontal_8(src, first, last - first, &ch));
        offset = first;
    }
    if need_v {
        let from = current.as_ref().unwrap_or(src);
        return vertical_8(from, offset, &cv);
    }
    current.unwrap_or_else(|| src.clone())
}

/// Geometry.c's ImagingScaleAffine: NEAREST, each output pixel the input's under its middle
/// (the positions summed step by step, as Geometry.c sums them).
fn nearest(src: &Image, w: usize, h: usize, bx: [f32; 4]) -> Image {
    let a0 = f64::from(bx[2] - bx[0]) / w as f64;
    let a4 = f64::from(bx[3] - bx[1]) / h as f64;
    let coord = |v: f64| -> i64 {
        if v < 0.0 {
            -1
        } else {
            v as i64
        }
    };
    let mut out = Image::blank(src.mode, w, h);
    let mut xin = vec![None; w];
    let mut xo = f64::from(bx[0]) + a0 * 0.5;
    for slot in xin.iter_mut() {
        let x = coord(xo);
        if x >= 0 && (x as usize) < src.width {
            *slot = Some(x as usize);
        }
        xo += a0;
    }
    let mut yo = f64::from(bx[1]) + a4 * 0.5;
    for y in 0..h {
        let yi = coord(yo);
        if yi >= 0 && (yi as usize) < src.height {
            let line = src.row(yi as usize);
            let dst = out.row_mut(y);
            match src.bands() {
                1 => nearest_row::<1>(line, dst, &xin),
                3 => nearest_row::<3>(line, dst, &xin),
                _ => nearest_row::<4>(line, dst, &xin),
            }
        }
        yo += a4;
    }
    out
}

/// One row of NEAREST: each pixel's `B` bytes from the input's under it (none where it has
/// none: left as it is).
#[inline(always)]
fn nearest_row<const B: usize>(line: &[u8], dst: &mut [u8], xin: &[Option<usize>]) {
    for (o, slot) in dst.chunks_exact_mut(B).zip(xin) {
        if let Some(sx) = *slot {
            let from: &[u8; B] = line[sx * B..sx * B + B].try_into().expect("B bytes");
            o.copy_from_slice(from);
        }
    }
}

/// _imaging.c's _resize, with the box as the floats it parses.
fn core_resize(src: &Image, w: usize, h: usize, filter: Filter, bx: [f32; 4]) -> Result<Image> {
    if w < 1 || h < 1 {
        return error("height and width must be > 0");
    }
    if bx[0] < 0.0 || bx[1] < 0.0 {
        return error("box offset can't be negative");
    }
    if bx[2] > src.width as f32 || bx[3] > src.height as f32 {
        return error("box can't exceed original image size");
    }
    if bx[2] - bx[0] < 0.0 || bx[3] - bx[1] < 0.0 {
        return error("box can't be empty");
    }
    let whole = |v: f32| v - (v as i32) as f32 == 0.0;
    if whole(bx[0]) && bx[2] - bx[0] == w as f32 && whole(bx[1]) && bx[3] - bx[1] == h as f32 {
        let c = |v: f32| v as i32 as i64;
        return Ok(src.crop(c(bx[0]), c(bx[1]), c(bx[2]), c(bx[3])));
    }
    if filter == Filter::Nearest {
        return Ok(nearest(src, w, h, bx));
    }
    Ok(resample_8(src, w, h, filter, bx))
}

impl Image {
    /// Image.resize((w, h), filter, box, reducing_gap); ``bounds`` (left, top, right, bottom) in
    /// the doubles Python computes with, the whole picture where None.
    pub fn resize(
        &self,
        w: usize,
        h: usize,
        filter: Filter,
        bounds: Option<(f64, f64, f64, f64)>,
        reducing_gap: Option<f64>,
    ) -> Result<Image> {
        if reducing_gap.is_some_and(|g| g < 1.0) {
            return error("reducing_gap must be 1.0 or greater");
        }
        let full = (0.0, 0.0, self.width as f64, self.height as f64);
        let mut bx = bounds.unwrap_or(full);
        if (w, h) == self.size() && bx == full {
            return Ok(self.clone());
        }
        if self.mode == Mode::RGBA && filter != Filter::Nearest {
            return self
                .convert(Mode::RGBa)?
                .resize(w, h, filter, Some(bx), reducing_gap)?
                .convert(Mode::RGBA);
        }
        let reduced;
        let mut src = self;
        if let (Some(gap), false) = (reducing_gap, filter == Filter::Nearest) {
            let fx = (((bx.2 - bx.0) / w as f64 / gap) as i64).max(1) as usize;
            let fy = (((bx.3 - bx.1) / h as f64 / gap) as i64).max(1) as usize;
            if fx > 1 || fy > 1 {
                let safe = self.safe_box(w, h, filter, bx);
                reduced = self.reduce(fx, fy, Some(safe))?;
                src = &reduced;
                bx = (
                    (bx.0 - safe.0 as f64) / fx as f64,
                    (bx.1 - safe.1 as f64) / fy as f64,
                    (bx.2 - safe.0 as f64) / fx as f64,
                    (bx.3 - safe.1 as f64) / fy as f64,
                );
            }
        }
        let f = [bx.0 as f32, bx.1 as f32, bx.2 as f32, bx.3 as f32];
        core_resize(src, w, h, filter, f)
    }

    /// Image._get_safe_box: the box with the pixels round it that the filter reaches.
    fn safe_box(
        &self,
        w: usize,
        h: usize,
        filter: Filter,
        bx: (f64, f64, f64, f64),
    ) -> (usize, usize, usize, usize) {
        let support = filter.support() - 0.5;
        let sx = support * ((bx.2 - bx.0) / w as f64);
        let sy = support * ((bx.3 - bx.1) / h as f64);
        (
            ((bx.0 - sx) as i64).max(0) as usize,
            ((bx.1 - sy) as i64).max(0) as usize,
            ((bx.2 + sx).ceil() as i64).min(self.width as i64) as usize,
            ((bx.3 + sy).ceil() as i64).min(self.height as i64) as usize,
        )
    }
}

/// The places ``from..to`` of ``c``, their inputs counted from the first any of them takes; and
/// the inputs they take (``first..end`` of the picture's).
fn window_of(c: &Coeffs, from: usize, to: usize) -> (Coeffs, usize, usize) {
    let bounds = &c.bounds[from..to];
    let first = bounds.iter().map(|b| b.0).min().unwrap_or(0);
    let end = bounds.iter().map(|b| b.0 + b.1).max().unwrap_or(first);
    let cut = Coeffs {
        ksize: c.ksize,
        bounds: bounds.iter().map(|&(m, n)| (m - first, n)).collect(),
        kk: c.kk[from * c.ksize..to * c.ksize].to_vec(),
    };
    (cut, first, end)
}

/// resample_8 of a window: ``src`` the inputs it takes, ``cv`` and ``ch`` its places (None where
/// that pass is not made), the passes in the order the whole picture's sizes ask for.
fn resample_8_window(
    src: &Image,
    rows_first: bool,
    cv: Option<&Coeffs>,
    ch: Option<&Coeffs>,
) -> Image {
    #[cfg(target_arch = "x86_64")]
    {
        static READY: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
        if *READY.get_or_init(|| std::arch::is_x86_feature_detected!("avx2")) {
            // SAFETY: the processor has AVX2.
            return unsafe { resample_8_window_avx2(src, rows_first, cv, ch) };
        }
    }
    resample_8_window_body(src, rows_first, cv, ch)
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn resample_8_window_avx2(
    src: &Image,
    rows_first: bool,
    cv: Option<&Coeffs>,
    ch: Option<&Coeffs>,
) -> Image {
    resample_8_window_body(src, rows_first, cv, ch)
}

#[inline(always)]
fn resample_8_window_body(
    src: &Image,
    rows_first: bool,
    cv: Option<&Coeffs>,
    ch: Option<&Coeffs>,
) -> Image {
    match (cv, ch) {
        (Some(cv), Some(ch)) if !rows_first => {
            horizontal_8(&vertical_8(src, 0, cv), 0, cv.bounds.len(), ch)
        }
        (Some(cv), Some(ch)) => vertical_8(&horizontal_8(src, 0, src.height, ch), 0, cv),
        (Some(cv), None) => vertical_8(src, 0, cv),
        (None, Some(ch)) => horizontal_8(src, 0, src.height, ch),
        (None, None) => src.clone(),
    }
}

impl Image {
    /// resize((w, h), filter) cut to ``window`` (left, top, right, bottom of the resized
    /// picture): only the window's pixels made, and only of the inputs they take. Each is the
    /// whole resize's: Resample.c makes every pixel of a pass of the inputs under its weights
    /// alone, the first pass rounded to 8 bits either way, and the passes go in the order the
    /// whole picture's sizes ask for.
    pub fn resize_window(
        &self,
        w: usize,
        h: usize,
        filter: Filter,
        window: (usize, usize, usize, usize),
    ) -> Result<Image> {
        let (x0, y0, x1, y1) = window;
        if x0 >= x1 || y0 >= y1 || x1 > w || y1 > h {
            return error("the window is not within the resized picture");
        }
        if filter == Filter::Nearest || (w, h) == self.size() || window == (0, 0, w, h) {
            let whole = self.resize(w, h, filter, None, None)?;
            return Ok(whole.crop(x0 as i64, y0 as i64, x1 as i64, y1 as i64));
        }
        let (in_w, in_h) = self.size();
        let cut = |size: usize, out: usize, from: usize, to: usize| {
            let (c, first, end) = window_of(&coeffs(size, 0.0, size as f32, out, filter), from, to);
            (Some(c), first, end)
        };
        let (cv, ya, yb) = if h != in_h {
            cut(in_h, h, y0, y1)
        } else {
            (None, y0, y1)
        };
        let (ch, xa, xb) = if w != in_w {
            cut(in_w, w, x0, x1)
        } else {
            (None, x0, x1)
        };
        let mut inputs = self.crop(xa as i64, ya as i64, xb as i64, yb as i64);
        let rgba = self.mode == Mode::RGBA; // (premultiplied, as resize takes it)
        if rgba {
            inputs = inputs.convert(Mode::RGBa)?;
        }
        let rows_first = horizontal_first(in_w, in_h, w, h);
        let made = resample_8_window(&inputs, rows_first, cv.as_ref(), ch.as_ref());
        if rgba {
            return made.convert(Mode::RGBA);
        }
        Ok(made)
    }
}

/// The rows of a float picture from ``first`` on (``rows`` of them), each resampled to ``c``'s
/// width: doubles summed, the result a float.
fn horizontal_f(src: &FloatImage, first: usize, rows: usize, c: &Coeffs) -> FloatImage {
    let w = c.bounds.len();
    let mut out = FloatImage::new(w, rows);
    for yy in 0..rows {
        let line = &src.data[(yy + first) * src.width..(yy + first + 1) * src.width];
        for (xx, &(xmin, xmax)) in c.bounds.iter().enumerate() {
            let k = &c.kk[xx * c.ksize..xx * c.ksize + xmax];
            let mut ss = 0.0f64;
            for (x, &kx) in k.iter().enumerate() {
                ss += f64::from(line[x + xmin]) * kx;
            }
            out.data[yy * w + xx] = ss as f32;
        }
    }
    out
}

/// The columns of a float picture resampled to ``c``'s height (its bounds shifted by
/// ``offset``).
fn vertical_f(src: &FloatImage, offset: usize, c: &Coeffs) -> FloatImage {
    let h = c.bounds.len();
    let mut out = FloatImage::new(src.width, h);
    for (yy, &(ymin, ymax)) in c.bounds.iter().enumerate() {
        let k = &c.kk[yy * c.ksize..yy * c.ksize + ymax];
        for xx in 0..src.width {
            let mut ss = 0.0f64;
            for (y, &ky) in k.iter().enumerate() {
                ss += f64::from(src.data[(y + ymin - offset) * src.width + xx]) * ky;
            }
            out.data[yy * src.width + xx] = ss as f32;
        }
    }
    out
}

/// Resample.c's passes for 32-bit floats (mode F), in the order ImagingResampleInner takes.
fn resample_f(src: &FloatImage, w: usize, h: usize, filter: Filter, bx: [f32; 4]) -> FloatImage {
    let need_h = w != src.width || bx[0] != 0.0 || bx[2] != w as f32;
    let need_v = h != src.height || bx[1] != 0.0 || bx[3] != h as f32;
    let cv = coeffs(src.height, bx[1], bx[3], h, filter);
    if !horizontal_first(src.width, src.height, w, h) {
        let tall = vertical_f(src, 0, &cv);
        if need_h {
            let ch = coeffs(src.width, bx[0], bx[2], w, filter);
            return horizontal_f(&tall, 0, h, &ch);
        }
        return tall;
    }
    let first = cv.bounds[0].0;
    let last = cv.bounds[h - 1].0 + cv.bounds[h - 1].1;
    let mut current: Option<FloatImage> = None;
    let mut offset = 0;
    if need_h {
        let ch = coeffs(src.width, bx[0], bx[2], w, filter);
        current = Some(horizontal_f(src, first, last - first, &ch));
        offset = first;
    }
    if need_v {
        return vertical_f(current.as_ref().unwrap_or(src), offset, &cv);
    }
    current.unwrap_or_else(|| src.clone())
}

impl FloatImage {
    /// Image.resize for mode F (no reducing_gap: Libre Panel asks for none there).
    pub fn resize(
        &self,
        w: usize,
        h: usize,
        filter: Filter,
        bounds: Option<(f64, f64, f64, f64)>,
    ) -> Result<FloatImage> {
        let full = (0.0, 0.0, self.width as f64, self.height as f64);
        let bx = bounds.unwrap_or(full);
        if (w, h) == (self.width, self.height) && bx == full {
            return Ok(self.clone());
        }
        let bx = [bx.0 as f32, bx.1 as f32, bx.2 as f32, bx.3 as f32];
        if w < 1 || h < 1 {
            return error("height and width must be > 0");
        }
        if bx[0] < 0.0 || bx[1] < 0.0 {
            return error("box offset can't be negative");
        }
        if bx[2] > self.width as f32 || bx[3] > self.height as f32 {
            return error("box can't exceed original image size");
        }
        if bx[2] - bx[0] < 0.0 || bx[3] - bx[1] < 0.0 {
            return error("box can't be empty");
        }
        let whole = |v: f32| v - (v as i32) as f32 == 0.0;
        if whole(bx[0]) && bx[2] - bx[0] == w as f32 && whole(bx[1]) && bx[3] - bx[1] == h as f32 {
            return Ok(self.crop(bx[0] as usize, bx[1] as usize, w, h));
        }
        if filter == Filter::Nearest {
            let a0 = f64::from(bx[2] - bx[0]) / w as f64;
            let a4 = f64::from(bx[3] - bx[1]) / h as f64;
            let mut out = FloatImage::new(w, h);
            let mut yo = f64::from(bx[1]) + a4 * 0.5;
            for y in 0..h {
                let mut xo = f64::from(bx[0]) + a0 * 0.5;
                for x in 0..w {
                    let (xi, yi) = (xo as i64, yo as i64);
                    if xo >= 0.0
                        && yo >= 0.0
                        && (xi as usize) < self.width
                        && (yi as usize) < self.height
                    {
                        out.data[y * w + x] = self.data[yi as usize * self.width + xi as usize];
                    }
                    xo += a0;
                }
                yo += a4;
            }
            return Ok(out);
        }
        Ok(resample_f(self, w, h, filter, bx))
    }

    fn crop(&self, x0: usize, y0: usize, w: usize, h: usize) -> FloatImage {
        let mut out = FloatImage::new(w, h);
        for y in 0..h {
            for x in 0..w {
                out.data[y * w + x] = self.data[(y0 + y) * self.width + x0 + x];
            }
        }
        out
    }
}

/// A 16-bit grey's two bytes (I;16: the low first) as the number.
#[inline(always)]
fn grey16(line: &[u8], x: usize) -> f64 {
    f64::from(u32::from(line[x * 2]) | u32::from(line[x * 2 + 1]) << 8)
}

/// Resample.c's end of a 16-bit sum: CLIP16(ROUND_UP(ss)), the low byte first.
#[inline(always)]
fn put16(out: &mut [u8], at: usize, ss: f64) {
    let v = (if ss >= 0.0 { ss + 0.5 } else { ss - 0.5 } as i32).clamp(0, 65535);
    out[at * 2] = (v & 0xff) as u8;
    out[at * 2 + 1] = (v >> 8) as u8;
}

/// ImagingResampleHorizontal_16bpc: rows ``first..first + rows`` of an I;16 picture
/// ``width`` wide, each resampled to ``c``'s width (doubles summed).
fn horizontal_16(src: &[u8], width: usize, first: usize, rows: usize, c: &Coeffs) -> Vec<u8> {
    let w = c.bounds.len();
    let mut out = vec![0u8; w * rows * 2];
    for yy in 0..rows {
        let line = &src[(yy + first) * width * 2..(yy + first + 1) * width * 2];
        for (xx, &(xmin, xmax)) in c.bounds.iter().enumerate() {
            let k = &c.kk[xx * c.ksize..xx * c.ksize + xmax];
            let mut ss = 0.0f64;
            for (x, &kx) in k.iter().enumerate() {
                ss += grey16(line, x + xmin) * kx;
            }
            put16(&mut out, yy * w + xx, ss);
        }
    }
    out
}

/// ImagingResampleVertical_16bpc: the columns of an I;16 picture ``width`` wide, resampled to
/// ``c``'s height (its bounds shifted by ``offset``).
fn vertical_16(src: &[u8], width: usize, offset: usize, c: &Coeffs) -> Vec<u8> {
    let h = c.bounds.len();
    let mut out = vec![0u8; width * h * 2];
    for (yy, &(ymin, ymax)) in c.bounds.iter().enumerate() {
        let k = &c.kk[yy * c.ksize..yy * c.ksize + ymax];
        for xx in 0..width {
            let mut ss = 0.0f64;
            for (y, &ky) in k.iter().enumerate() {
                let row = (y + ymin - offset) * width * 2;
                ss += grey16(&src[row..row + width * 2], xx) * ky;
            }
            put16(&mut out, yy * width + xx, ss);
        }
    }
    out
}

/// Image.resize((w, h), filter, box, reducing_gap) of an I;16 picture (``src``, two bytes a
/// pixel, the low first, ``width`` x ``height``) as Pillow 13.0 makes it: NEAREST through
/// Geometry.c's generic transform (each pixel's place worked out by itself), the other
/// filters through Resample.c's 16-bit passes; a reducing_gap that would reduce first is
/// refused, as ImagingReduce refuses I;16.
#[allow(clippy::too_many_arguments)]
pub fn resize_16(
    src: &[u8],
    width: usize,
    height: usize,
    w: usize,
    h: usize,
    filter: Filter,
    bounds: Option<(f64, f64, f64, f64)>,
    reducing_gap: Option<f64>,
) -> Result<Vec<u8>> {
    if src.len() != width * height * 2 {
        return error("wrong size of I;16 data");
    }
    if reducing_gap.is_some_and(|g| g < 1.0) {
        return error("reducing_gap must be 1.0 or greater");
    }
    let full = (0.0, 0.0, width as f64, height as f64);
    let bx = bounds.unwrap_or(full);
    if (w, h) == (width, height) && bx == full {
        return Ok(src.to_vec());
    }
    if let (Some(gap), false) = (reducing_gap, filter == Filter::Nearest) {
        let fx = ((bx.2 - bx.0) / w as f64 / gap) as i64;
        let fy = ((bx.3 - bx.1) / h as f64 / gap) as i64;
        if fx > 1 || fy > 1 {
            return error("image has wrong mode");
        }
    }
    let bx = [bx.0 as f32, bx.1 as f32, bx.2 as f32, bx.3 as f32];
    if w < 1 || h < 1 {
        return error("height and width must be > 0");
    }
    if bx[0] < 0.0 || bx[1] < 0.0 {
        return error("box offset can't be negative");
    }
    if bx[2] > width as f32 || bx[3] > height as f32 {
        return error("box can't exceed original image size");
    }
    if bx[2] - bx[0] < 0.0 || bx[3] - bx[1] < 0.0 {
        return error("box can't be empty");
    }
    let whole = |v: f32| v - (v as i32) as f32 == 0.0;
    if whole(bx[0]) && bx[2] - bx[0] == w as f32 && whole(bx[1]) && bx[3] - bx[1] == h as f32 {
        let (x0, y0) = (bx[0] as usize, bx[1] as usize);
        let mut out = vec![0u8; w * h * 2];
        for y in 0..h {
            let from = ((y0 + y) * width + x0) * 2;
            out[y * w * 2..(y + 1) * w * 2].copy_from_slice(&src[from..from + w * 2]);
        }
        return Ok(out);
    }
    if filter == Filter::Nearest {
        let a0 = f64::from(bx[2] - bx[0]) / w as f64;
        let a4 = f64::from(bx[3] - bx[1]) / h as f64;
        let (a2, a5) = (f64::from(bx[0]), f64::from(bx[1]));
        let coord = |v: f64| if v < 0.0 { -1 } else { v as i64 };
        let mut out = vec![0u8; w * h * 2];
        for y in 0..h {
            let yi = coord(a4 * (y as f64 + 0.5) + a5);
            if yi < 0 || yi as usize >= height {
                continue;
            }
            for x in 0..w {
                let xi = coord(a0 * (x as f64 + 0.5) + a2);
                if xi >= 0 && (xi as usize) < width {
                    let from = (yi as usize * width + xi as usize) * 2;
                    out[(y * w + x) * 2..(y * w + x) * 2 + 2].copy_from_slice(&src[from..from + 2]);
                }
            }
        }
        return Ok(out);
    }
    let need_h = w != width || bx[0] != 0.0 || bx[2] != w as f32;
    let need_v = h != height || bx[1] != 0.0 || bx[3] != h as f32;
    let cv = coeffs(height, bx[1], bx[3], h, filter);
    if !horizontal_first(width, height, w, h) {
        let tall = vertical_16(src, width, 0, &cv);
        if need_h {
            let ch = coeffs(width, bx[0], bx[2], w, filter);
            return Ok(horizontal_16(&tall, width, 0, h, &ch));
        }
        return Ok(tall);
    }
    let first = cv.bounds[0].0;
    let last = cv.bounds[h - 1].0 + cv.bounds[h - 1].1;
    if need_h {
        let ch = coeffs(width, bx[0], bx[2], w, filter);
        let wide = horizontal_16(src, width, first, last - first, &ch);
        if need_v {
            return Ok(vertical_16(&wide, w, first, &cv));
        }
        return Ok(wide);
    }
    if need_v {
        return Ok(vertical_16(src, width, 0, &cv));
    }
    Ok(src.to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_window_of_a_resize_is_the_resize_cut_to_it() {
        let mut seed = 0x9e37_79b9_7f4a_7c15_u64;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            (seed >> 16) as u8
        };
        // (smaller, larger, the columns first where the height shrinks most, one way only)
        let sizes = [
            ((40, 30), (25, 19)),
            ((40, 30), (83, 61)),
            ((40, 30), (40, 11)),
            ((40, 30), (17, 30)),
            ((40, 30), (40, 47)),
            ((7, 90), (13, 20)),
            ((120, 9), (31, 9)),
        ];
        let filters = [
            Filter::Bicubic,
            Filter::Bilinear,
            Filter::Lanczos,
            Filter::Box,
            Filter::Hamming,
            Filter::Nearest,
        ];
        for mode in [Mode::L, Mode::RGB, Mode::RGBA, Mode::RGBa] {
            for ((iw, ih), (w, h)) in sizes {
                let mut src = Image::blank(mode, iw, ih);
                for v in src.data.iter_mut() {
                    *v = next();
                }
                for filter in filters {
                    let whole = src.resize(w, h, filter, None, None).unwrap();
                    for window in [
                        (0, 0, w, h),
                        (0, 0, w / 2, h),
                        (w / 3, 0, w, h),
                        (0, h / 2, w, h),
                        (w / 4, h / 3, w - w / 5, h - 1),
                        (w - 1, h - 1, w, h),
                        (1, 1, 2, 2),
                    ] {
                        let (x0, y0, x1, y1) = window;
                        let cut = whole.crop(x0 as i64, y0 as i64, x1 as i64, y1 as i64);
                        let got = src.resize_window(w, h, filter, window).unwrap();
                        assert!(
                            got == cut,
                            "{mode:?} {iw}x{ih} to {w}x{h} {filter:?} {window:?}"
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn a_window_not_within_the_resized_picture_is_refused() {
        let src = Image::blank(Mode::L, 4, 4);
        for window in [(0, 0, 0, 2), (1, 1, 9, 2), (2, 2, 1, 3)] {
            assert!(
                src.resize_window(8, 8, Filter::Bicubic, window).is_err(),
                "{window:?}"
            );
        }
    }
}
