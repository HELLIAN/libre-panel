//! Image.transpose, Image.transform and Image.rotate (libImaging/Geometry.c, PIL/Image.py):
//! a transpose as each pixel's new place; a transform as Geometry.c maps the middle of each
//! pixel it makes back into the picture and takes what lies there (nearest, or bilinear or
//! bicubic in its doubles); a rotation as Image.py's matrix for it, its numbers rounded to 15
//! places as Python rounds them.

use crate::resample::Filter;
use crate::{error, Image, Mode, Result};

/// Image.Transpose.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Transpose {
    FlipLeftRight,
    FlipTopBottom,
    Rotate90,
    Rotate180,
    Rotate270,
    Transpose,
    Transverse,
}

impl Transpose {
    /// Pillow's number for it (Image.Transpose's values).
    pub fn from_number(n: i64) -> Option<Transpose> {
        use Transpose as T;
        Some(match n {
            0 => T::FlipLeftRight,
            1 => T::FlipTopBottom,
            2 => T::Rotate90,
            3 => T::Rotate180,
            4 => T::Rotate270,
            5 => T::Transpose,
            6 => T::Transverse,
            _ => return None,
        })
    }
}

/// Image.Transform with its data: AFFINE (a, b, c, d, e, f: the input point of an output
/// one), EXTENT (the input box the output covers), PERSPECTIVE, QUAD (the input's corners
/// for the output's top left, bottom left, bottom right and top right).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Transform {
    Affine([f64; 6]),
    Extent([f64; 4]),
    Perspective([f64; 8]),
    Quad([f64; 8]),
}

/// Python's x % m for floats (the sign of m, never -0).
fn py_mod(x: f64, m: f64) -> f64 {
    let r = x % m;
    if r == 0.0 {
        0.0f64.copysign(m)
    } else if (m < 0.0) != (r < 0.0) {
        r + m
    } else {
        r
    }
}

/// Python's round(x, digits) for a float: the decimal of `digits` places nearest to it (half
/// to even), as the double nearest to that.
fn py_round(x: f64, digits: usize) -> f64 {
    lp_py::round_digits(x, digits)
}

/// Geometry.c's COORD: where a coordinate falls, -1 for any left of 0.
#[inline(always)]
fn coord(v: f64) -> i32 {
    if v < 0.0 {
        -1
    } else {
        v as i32
    }
}

/// The pixel at (x, y) of `src` copied to (ox, oy) of `out` (both of the same mode).
#[inline(always)]
fn copy_pixel(src: &Image, x: usize, y: usize, out: &mut Image, ox: usize, oy: usize) {
    let b = src.bands();
    let (s, d) = ((y * src.width + x) * b, (oy * out.width + ox) * b);
    out.data[d..d + b].copy_from_slice(&src.data[s..s + b]);
}

/// Geometry.c's bilinear filters: the four pixels round (xin, yin) weighed by where it lies
/// between them, band by band, the edges held; false off the picture.
fn bilinear(im: &Image, xin: f64, yin: f64, out: &mut [u8]) -> bool {
    let (w, h) = (im.width as i32, im.height as i32);
    if xin < 0.0 || xin >= f64::from(w) || yin < 0.0 || yin >= f64::from(h) {
        return false;
    }
    let (xin, yin) = (xin - 0.5, yin - 0.5);
    let (x, y) = (xin.floor() as i32, yin.floor() as i32);
    let (dx, dy) = (xin - f64::from(x), yin - f64::from(y));
    let b = im.bands();
    let clip_x = |v: i32| v.clamp(0, w - 1) as usize * b;
    let (x0, x1) = (clip_x(x), clip_x(x + 1));
    let first = im.row(y.clamp(0, h - 1) as usize);
    let second = (y + 1 >= 0 && y + 1 < h).then(|| im.row((y + 1) as usize));
    let lerp = |a: u8, c: u8| f64::from(a) + f64::from(i32::from(c) - i32::from(a)) * dx;
    for (band, o) in out.iter_mut().enumerate().take(b) {
        let v1 = lerp(first[x0 + band], first[x1 + band]);
        let v2 = second.map_or(v1, |r| lerp(r[x0 + band], r[x1 + band]));
        *o = (v1 + (v2 - v1) * dy) as u8;
    }
    true
}

/// Geometry.c's BICUBIC on four values (the cubic through them at d, between the second and
/// the third), in its order of operations.
#[inline(always)]
fn cubic(v1: f64, v2: f64, v3: f64, v4: f64, d: f64) -> f64 {
    let p1 = v2;
    let p2 = -v1 + v3;
    let p3 = 2.0 * (v1 - v2) + v3 - v4;
    let p4 = -v1 + v2 - v3 + v4;
    p1 + d * (p2 + d * (p3 + d * p4))
}

/// The same on four bytes, its sums as C makes them (in ints, then doubles).
#[inline(always)]
fn cubic8(v: [u8; 4], d: f64) -> f64 {
    let [v1, v2, v3, v4] = v.map(i32::from);
    let p1 = f64::from(v2);
    let p2 = f64::from(-v1 + v3);
    let p3 = f64::from(2 * (v1 - v2) + v3 - v4);
    let p4 = f64::from(-v1 + v2 - v3 + v4);
    p1 + d * (p2 + d * (p3 + d * p4))
}

/// floor of a double as an int, for one within an int's reach (what is no number, 0): what
/// `v.floor() as i32` gives, without a call to floor where the processor has no instruction
/// for it.
#[inline(always)]
fn floor_i32(v: f64) -> i32 {
    let i = v as i32;
    if f64::from(i) > v {
        i - 1
    } else {
        i
    }
}

/// bilinear of a picture of `B` bands: the same sums in the same order.
#[inline(always)]
fn bilinear_n<const B: usize>(im: &Image, xin: f64, yin: f64, out: &mut [u8; B]) -> bool {
    let (w, h) = (im.width as i32, im.height as i32);
    if xin < 0.0 || xin >= f64::from(w) || yin < 0.0 || yin >= f64::from(h) {
        return false;
    }
    let (xin, yin) = (xin - 0.5, yin - 0.5);
    let (x, y) = (floor_i32(xin), floor_i32(yin));
    let (dx, dy) = (xin - f64::from(x), yin - f64::from(y));
    let clip_x = |v: i32| v.clamp(0, w - 1) as usize * B;
    let (x0, x1) = (clip_x(x), clip_x(x + 1));
    let first = im.row(y.clamp(0, h - 1) as usize);
    let second = (y + 1 >= 0 && y + 1 < h).then(|| im.row((y + 1) as usize));
    let lerp = |a: u8, c: u8| f64::from(a) + f64::from(i32::from(c) - i32::from(a)) * dx;
    for (band, o) in out.iter_mut().enumerate() {
        let v1 = lerp(first[x0 + band], first[x1 + band]);
        let v2 = second.map_or(v1, |r| lerp(r[x0 + band], r[x1 + band]));
        *o = (v1 + (v2 - v1) * dy) as u8;
    }
    true
}

/// ImagingGenericTransform with the bilinear filter on a picture of `B` bands.
fn each<const B: usize>(src: &Image, out: &mut Image, map: Map) {
    let w = out.width;
    if w == 0 || out.height == 0 {
        return;
    }
    let mut px = [0u8; B];
    for (y, row) in out.data.chunks_exact_mut(w * B).enumerate() {
        for (x, o) in row.chunks_exact_mut(B).enumerate() {
            let (xx, yy) = map.at(x, y);
            if bilinear_n::<B>(src, xx, yy, &mut px) {
                o.copy_from_slice(&px);
            }
        }
    }
}

/// ImagingGenericTransform with the bilinear filter on four bands, the four of a pixel at once
/// where the processor has AVX2: the same doubles in the same order as `bilinear_n::<4>`.
#[cfg(target_arch = "x86_64")]
mod wide {
    use super::{floor_i32, Map};
    use crate::Image;
    use std::arch::x86_64::*;

    /// Whether the processor has what `each4` takes.
    pub fn ready() -> bool {
        static READY: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
        *READY.get_or_init(|| std::arch::is_x86_feature_detected!("avx2"))
    }

    /// A pixel's four bytes at `at` as four ints (`at` the start of a pixel of `data`: of a
    /// column and a row each held within the picture).
    #[inline(always)]
    unsafe fn four(data: &[u8], at: usize) -> __m128i {
        debug_assert!(at + 4 <= data.len());
        let p = data.as_ptr().add(at).cast::<i32>().read_unaligned();
        _mm_cvtepu8_epi32(_mm_cvtsi32_si128(p))
    }

    /// `a + (c - a) * dx`, band by band, as doubles (the difference made in ints first).
    #[inline(always)]
    unsafe fn lerp(a: __m128i, c: __m128i, dx: __m256d) -> __m256d {
        _mm256_add_pd(
            _mm256_cvtepi32_pd(a),
            _mm256_mul_pd(_mm256_cvtepi32_pd(_mm_sub_epi32(c, a)), dx),
        )
    }

    /// One pixel's four bands at (xin, yin) of `src` into `o`, where that lies on it.
    #[inline(always)]
    unsafe fn pixel(src: &Image, xin: f64, yin: f64, o: &mut [u8]) {
        let (sw, sh) = (src.width as i32, src.height as i32);
        if xin < 0.0 || xin >= f64::from(sw) || yin < 0.0 || yin >= f64::from(sh) {
            return;
        }
        let stride = src.width * 4;
        let data = &src.data;
        let (xin, yin) = (xin - 0.5, yin - 0.5);
        let (xi, yi) = (floor_i32(xin), floor_i32(yin));
        let (dx, dy) = (xin - f64::from(xi), yin - f64::from(yi));
        let x0 = xi.clamp(0, sw - 1) as usize * 4;
        let x1 = (xi + 1).clamp(0, sw - 1) as usize * 4;
        let first = yi.clamp(0, sh - 1) as usize * stride;
        let dxv = _mm256_set1_pd(dx);
        let v1 = lerp(four(data, first + x0), four(data, first + x1), dxv);
        let v2 = if yi + 1 >= 0 && yi + 1 < sh {
            let second = (yi + 1) as usize * stride;
            lerp(four(data, second + x0), four(data, second + x1), dxv)
        } else {
            v1
        };
        let v = _mm256_add_pd(v1, _mm256_mul_pd(_mm256_sub_pd(v2, v1), _mm256_set1_pd(dy)));
        // (cut to an int toward 0, then held within 0 and 255: what `as u8` gives, no number
        // at all 0)
        let ints = _mm256_cvttpd_epi32(v);
        let bytes = _mm_packus_epi16(_mm_packs_epi32(ints, ints), _mm_setzero_si128());
        o.copy_from_slice(&_mm_cvtsi128_si32(bytes).to_le_bytes());
    }

    #[target_feature(enable = "avx2")]
    pub unsafe fn each4(src: &Image, out: &mut Image, map: Map) {
        let w = out.width;
        if w == 0 || out.height == 0 {
            return;
        }
        let Map::Affine(a) = map else {
            for (y, row) in out.data.chunks_exact_mut(w * 4).enumerate() {
                for (x, o) in row.chunks_exact_mut(4).enumerate() {
                    let (xin, yin) = map.at(x, y);
                    pixel(src, xin, yin, o);
                }
            }
            return;
        };
        // (affine: the places of four pixels of a row at once, each as Map::at sums it, and
        // four that all lie off the picture passed over together)
        let (sw, sh) = (
            _mm256_set1_pd(src.width as f64),
            _mm256_set1_pd(src.height as f64),
        );
        let zero = _mm256_setzero_pd();
        let (a0, a2) = (_mm256_set1_pd(a[0]), _mm256_set1_pd(a[2]));
        let (a3, a5) = (_mm256_set1_pd(a[3]), _mm256_set1_pd(a[5]));
        let lanes = _mm256_setr_pd(0.5, 1.5, 2.5, 3.5);
        for (y, row) in out.data.chunks_exact_mut(w * 4).enumerate() {
            let yin = y as f64 + 0.5;
            let (a1y, a4y) = (_mm256_set1_pd(a[1] * yin), _mm256_set1_pd(a[4] * yin));
            let mut x = 0;
            while x + 4 <= w {
                let xin = _mm256_add_pd(_mm256_set1_pd(x as f64), lanes);
                let xs = _mm256_add_pd(_mm256_add_pd(_mm256_mul_pd(a0, xin), a1y), a2);
                let ys = _mm256_add_pd(_mm256_add_pd(_mm256_mul_pd(a3, xin), a4y), a5);
                let off = _mm256_or_pd(
                    _mm256_or_pd(
                        _mm256_cmp_pd::<_CMP_LT_OQ>(xs, zero),
                        _mm256_cmp_pd::<_CMP_GE_OQ>(xs, sw),
                    ),
                    _mm256_or_pd(
                        _mm256_cmp_pd::<_CMP_LT_OQ>(ys, zero),
                        _mm256_cmp_pd::<_CMP_GE_OQ>(ys, sh),
                    ),
                );
                if _mm256_movemask_pd(off) != 0b1111 {
                    let (mut px, mut py) = ([0.0f64; 4], [0.0f64; 4]);
                    _mm256_storeu_pd(px.as_mut_ptr(), xs);
                    _mm256_storeu_pd(py.as_mut_ptr(), ys);
                    for k in 0..4 {
                        let at = (x + k) * 4;
                        pixel(src, px[k], py[k], &mut row[at..at + 4]);
                    }
                }
                x += 4;
            }
            for x in x..w {
                let (xin, yin) = map.at(x, y);
                pixel(src, xin, yin, &mut row[x * 4..x * 4 + 4]);
            }
        }
    }
}

/// Geometry.c's bicubic filters: the sixteen pixels round (xin, yin), rows beyond the picture
/// as the last within it, clipped to 0..255; false off the picture.
fn bicubic(im: &Image, xin: f64, yin: f64, out: &mut [u8]) -> bool {
    let (w, h) = (im.width as i32, im.height as i32);
    if xin < 0.0 || xin >= f64::from(w) || yin < 0.0 || yin >= f64::from(h) {
        return false;
    }
    let (xin, yin) = (xin - 0.5, yin - 0.5);
    let (x, y) = (xin.floor() as i32, yin.floor() as i32);
    let (dx, dy) = (xin - f64::from(x), yin - f64::from(y));
    let (x, y) = (x - 1, y - 1);
    let b = im.bands();
    let clip_x = |v: i32| v.clamp(0, w - 1) as usize * b;
    let xs = [clip_x(x), clip_x(x + 1), clip_x(x + 2), clip_x(x + 3)];
    let row = |r: i32| (r >= 0 && r < h).then(|| im.row(r as usize));
    let rows = [
        Some(im.row(y.clamp(0, h - 1) as usize)),
        row(y + 1),
        row(y + 2),
        row(y + 3),
    ];
    for (band, o) in out.iter_mut().enumerate().take(b) {
        let across = |r: &[u8]| cubic8(xs.map(|x| r[x + band]), dx);
        let v1 = across(rows[0].unwrap());
        let v2 = rows[1].map_or(v1, across);
        let v3 = rows[2].map_or(v2, across);
        let v4 = rows[3].map_or(v3, across);
        let v = cubic(v1, v2, v3, v4, dy);
        *o = if v <= 0.0 {
            0
        } else if v >= 255.0 {
            255
        } else {
            v as u8
        };
    }
    true
}

/// Geometry.c's nearest filter: the pixel under (xin, yin); false off the picture.
fn nearest(im: &Image, xin: f64, yin: f64, out: &mut [u8]) -> bool {
    let (x, y) = (coord(xin), coord(yin));
    if x < 0 || x as usize >= im.width || y < 0 || y as usize >= im.height {
        return false;
    }
    let b = im.bands();
    let at = (y as usize * im.width + x as usize) * b;
    out[..b].copy_from_slice(&im.data[at..at + b]);
    true
}

/// How a transform maps the middle of an output pixel into the input (Geometry.c's
/// affine_transform, perspective_transform, quad_transform).
#[derive(Clone, Copy)]
enum Map {
    Affine([f64; 6]),
    Perspective([f64; 8]),
    Quad([f64; 8]),
}

impl Map {
    #[inline(always)]
    fn at(&self, x: usize, y: usize) -> (f64, f64) {
        let (xin, yin) = (x as f64 + 0.5, y as f64 + 0.5);
        match self {
            Map::Affine(a) => (
                a[0] * xin + a[1] * yin + a[2],
                a[3] * xin + a[4] * yin + a[5],
            ),
            Map::Perspective(a) => (
                (a[0] * xin + a[1] * yin + a[2]) / (a[6] * xin + a[7] * yin + 1.0),
                (a[3] * xin + a[4] * yin + a[5]) / (a[6] * xin + a[7] * yin + 1.0),
            ),
            Map::Quad(a) => (
                a[0] + a[1] * xin + a[2] * yin + a[3] * xin * yin,
                a[4] + a[5] * xin + a[6] * yin + a[7] * xin * yin,
            ),
        }
    }
}

/// ImagingGenericTransform: each output pixel the filter's sample at its middle's place, left
/// as it was (the fill) where that lies off the picture.
fn generic(src: &Image, out: &mut Image, map: Map, filter: Filter) {
    // (bilinear, the most taken: each band count its own loop, the filter inlined)
    if filter == Filter::Bilinear {
        match src.bands() {
            1 => return each::<1>(src, out, map),
            3 => return each::<3>(src, out, map),
            4 => {
                #[cfg(target_arch = "x86_64")]
                if wide::ready() {
                    // SAFETY: the processor has AVX2 (ready).
                    unsafe { wide::each4(src, out, map) };
                    return;
                }
                return each::<4>(src, out, map);
            }
            _ => {}
        }
    }
    let sample: fn(&Image, f64, f64, &mut [u8]) -> bool = match filter {
        Filter::Bilinear => bilinear,
        Filter::Bicubic => bicubic,
        _ => nearest,
    };
    let b = src.bands();
    let mut px = [0u8; 4];
    for y in 0..out.height {
        for x in 0..out.width {
            let (xx, yy) = map.at(x, y);
            if sample(src, xx, yy, &mut px) {
                let at = (y * out.width + x) * b;
                out.data[at..at + b].copy_from_slice(&px[..b]);
            }
        }
    }
}

/// ImagingScaleAffine: nearest, a scale and a shift only; the places summed step by step as
/// Geometry.c sums them.
pub(crate) fn scale_affine(src: &Image, out: &mut Image, a: &[f64; 6]) {
    let mut xin: Vec<Option<usize>> = vec![None; out.width];
    let mut xo = a[2] + a[0] * 0.5;
    for slot in xin.iter_mut() {
        let x = coord(xo);
        if x >= 0 && (x as usize) < src.width {
            *slot = Some(x as usize);
        }
        xo += a[0];
    }
    let mut yo = a[5] + a[4] * 0.5;
    for y in 0..out.height {
        let yi = coord(yo);
        if yi >= 0 && (yi as usize) < src.height {
            for (x, slot) in xin.iter().enumerate() {
                if let Some(sx) = *slot {
                    copy_pixel(src, sx, yi as usize, out, x, y);
                }
            }
        }
        yo += a[4];
    }
}

/// Geometry.c's check_fixed: whether a corner maps within 16.16 fixed point's reach.
fn fits_fixed(a: &[f64; 6], x: usize, y: usize) -> bool {
    let (x, y) = (x as f64, y as f64);
    (x * a[0] + y * a[1] + a[2]).abs() < 32768.0 && (x * a[3] + y * a[4] + a[5]).abs() < 32768.0
}

/// Geometry.c's FIX: a double in 16.16 fixed point.
fn fix(v: f64) -> i32 {
    (v * 65536.0 + 0.5).floor() as i32
}

/// affine_fixed: nearest, the places in 16.16 fixed point.
fn affine_fixed(src: &Image, out: &mut Image, a: &[f64; 6]) {
    let (a0, a1, a3, a4) = (fix(a[0]), fix(a[1]), fix(a[3]), fix(a[4]));
    let mut a2 = fix(a[2] + a[0] * 0.5 + a[1] * 0.5);
    let mut a5 = fix(a[5] + a[3] * 0.5 + a[4] * 0.5);
    let (w, h) = (src.width as i32, src.height as i32);
    for y in 0..out.height {
        let (mut xx, mut yy) = (a2, a5);
        for x in 0..out.width {
            let xin = xx >> 16;
            if xin >= 0 && xin < w {
                let yin = yy >> 16;
                if yin >= 0 && yin < h {
                    copy_pixel(src, xin as usize, yin as usize, out, x, y);
                }
            }
            xx = xx.wrapping_add(a0);
            yy = yy.wrapping_add(a3);
        }
        a2 = a2.wrapping_add(a1);
        a5 = a5.wrapping_add(a4);
    }
}

/// ImagingTransformAffine's own loop where fixed point does not reach: nearest in doubles.
fn affine_float(src: &Image, out: &mut Image, a: &[f64; 6]) {
    let mut xo = a[2] + a[1] * 0.5 + a[0] * 0.5;
    let mut yo = a[5] + a[4] * 0.5 + a[3] * 0.5;
    for y in 0..out.height {
        let (mut xx, mut yy) = (xo, yo);
        for x in 0..out.width {
            let xin = coord(xx);
            if xin >= 0 && (xin as usize) < src.width {
                let yin = coord(yy);
                if yin >= 0 && (yin as usize) < src.height {
                    copy_pixel(src, xin as usize, yin as usize, out, x, y);
                }
            }
            xx += a[0];
            yy += a[3];
        }
        xo += a[1];
        yo += a[4];
    }
}

impl Image {
    /// Image.transpose(method).
    pub fn transpose(&self, method: Transpose) -> Image {
        use Transpose as T;
        let (w, h) = (self.width, self.height);
        let turned = matches!(
            method,
            T::Rotate90 | T::Rotate270 | T::Transpose | T::Transverse
        );
        let (ow, oh) = if turned { (h, w) } else { (w, h) };
        let mut out = Image::blank(self.mode, ow, oh);
        for y in 0..h {
            for x in 0..w {
                let (ox, oy) = match method {
                    T::FlipLeftRight => (w - 1 - x, y),
                    T::FlipTopBottom => (x, h - 1 - y),
                    T::Rotate90 => (y, w - 1 - x),
                    T::Rotate180 => (w - 1 - x, h - 1 - y),
                    T::Rotate270 => (h - 1 - y, x),
                    T::Transpose => (y, x),
                    T::Transverse => (h - 1 - y, w - 1 - x),
                };
                copy_pixel(self, x, y, &mut out, ox, oy);
            }
        }
        out
    }

    /// Image.transform(size, method, data, resample, fillcolor=fillcolor): RGBA other than
    /// nearest premultiplied (Image.py converts it to RGBa and back), what lies off the
    /// picture the fill colour (0 where none).
    pub fn transform(
        &self,
        size: (usize, usize),
        method: Transform,
        resample: Filter,
        fillcolor: Option<&[u8]>,
    ) -> Result<Image> {
        if self.mode == Mode::RGBA && resample != Filter::Nearest {
            return self
                .convert(Mode::RGBa)?
                .transform(size, method, resample, fillcolor)?
                .convert(Mode::RGBA);
        }
        let (w, h) = size;
        let map = match method {
            Transform::Affine(a) => Map::Affine(a),
            Transform::Extent([x0, y0, x1, y1]) => {
                if w == 0 || h == 0 {
                    return error("float division by zero");
                }
                let (xs, ys) = ((x1 - x0) / w as f64, (y1 - y0) / h as f64);
                Map::Affine([xs, 0.0, x0, 0.0, ys, y0])
            }
            Transform::Perspective(a) => Map::Perspective(a),
            Transform::Quad(q) => {
                if w == 0 || h == 0 {
                    return error("float division by zero");
                }
                let (nw, sw, se, ne) = ((q[0], q[1]), (q[2], q[3]), (q[4], q[5]), (q[6], q[7]));
                let (x0, y0) = nw;
                let (sx, sy) = (1.0 / w as f64, 1.0 / h as f64);
                Map::Quad([
                    x0,
                    (ne.0 - x0) * sx,
                    (sw.0 - x0) * sy,
                    (se.0 - sw.0 - ne.0 + x0) * sx * sy,
                    y0,
                    (ne.1 - y0) * sx,
                    (sw.1 - y0) * sy,
                    (se.1 - sw.1 - ne.1 + y0) * sx * sy,
                ])
            }
        };
        if !matches!(
            resample,
            Filter::Nearest | Filter::Bilinear | Filter::Bicubic
        ) {
            return error("this resampling filter cannot be used in a transform");
        }
        let mut out = match fillcolor {
            None => Image::blank(self.mode, w, h),
            Some(c) if c.len() == self.bands() => Image::new(self.mode, w, h, c),
            Some(_) => return error("a fill colour of another number of bands"),
        };
        match map {
            Map::Affine(a) if resample == Filter::Nearest => {
                if a[1] == 0.0 && a[3] == 0.0 {
                    scale_affine(self, &mut out, &a);
                } else if fits_fixed(&a, 0, 0)
                    && fits_fixed(&a, w, h)
                    && fits_fixed(&a, 0, h)
                    && fits_fixed(&a, w, 0)
                {
                    affine_fixed(self, &mut out, &a);
                } else {
                    affine_float(self, &mut out, &a);
                }
            }
            _ => generic(self, &mut out, map, resample),
        }
        Ok(out)
    }

    /// Image.rotate(angle, resample, expand, center, translate, fillcolor): counter
    /// clockwise, about the middle (or `center`), a quarter or a half turn a transpose.
    pub fn rotate(
        &self,
        angle: f64,
        resample: Filter,
        expand: bool,
        center: Option<(f64, f64)>,
        translate: Option<(f64, f64)>,
        fillcolor: Option<&[u8]>,
    ) -> Result<Image> {
        let angle = py_mod(angle, 360.0);
        if center.is_none() && translate.is_none() {
            if angle == 0.0 {
                return Ok(self.clone());
            }
            if angle == 180.0 {
                return Ok(self.transpose(Transpose::Rotate180));
            }
            if (angle == 90.0 || angle == 270.0) && (expand || self.width == self.height) {
                return Ok(self.transpose(if angle == 90.0 {
                    Transpose::Rotate90
                } else {
                    Transpose::Rotate270
                }));
            }
        }
        let (mut w, mut h) = (self.width, self.height);
        let post = translate.unwrap_or((0.0, 0.0));
        let center = center.unwrap_or((w as f64 / 2.0, h as f64 / 2.0));
        let angle = -angle.to_radians();
        let mut m = [
            py_round(angle.cos(), 15),
            py_round(angle.sin(), 15),
            0.0,
            py_round(-angle.sin(), 15),
            py_round(angle.cos(), 15),
            0.0,
        ];
        let apply =
            |x: f64, y: f64, m: &[f64; 6]| (m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]);
        (m[2], m[5]) = apply(-center.0 - post.0, -center.1 - post.1, &m);
        m[2] += center.0;
        m[5] += center.1;
        if expand {
            let (wf, hf) = (w as f64, h as f64);
            let corners =
                [(0.0, 0.0), (wf, 0.0), (wf, hf), (0.0, hf)].map(|(x, y)| apply(x, y, &m));
            let xs = corners.map(|c| c.0);
            let ys = corners.map(|c| c.1);
            let max = |v: [f64; 4]| v.into_iter().fold(f64::NEG_INFINITY, f64::max);
            let min = |v: [f64; 4]| v.into_iter().fold(f64::INFINITY, f64::min);
            let nw = max(xs).ceil() as i64 - min(xs).floor() as i64;
            let nh = max(ys).ceil() as i64 - min(ys).floor() as i64;
            let shift = |n: i64, was: usize| -((n - was as i64) as f64) / 2.0;
            (m[2], m[5]) = apply(shift(nw, w), shift(nh, h), &m);
            (w, h) = (nw.max(0) as usize, nh.max(0) as usize);
        }
        self.transform((w, h), Transform::Affine(m), resample, fillcolor)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[cfg(target_arch = "x86_64")]
    fn four_bands_at_once_as_one_by_one() {
        // (turned, grown and moved pictures of four bands, edges held, beyond the picture, a
        // map with no number in it: each pixel as the band-by-band filter makes it)
        if !wide::ready() {
            return;
        }
        let mut seed = 0x005e_ed0f_f00d_u64;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed
        };
        for (w, h) in [(1usize, 1usize), (2, 3), (17, 9), (130, 122)] {
            let mut src = Image::blank(Mode::RGBa, w, h);
            for v in src.data.iter_mut() {
                *v = (next() >> 24) as u8;
            }
            for k in 0..40 {
                let turn = (next() % 6283) as f64 / 1000.0;
                let size = 0.3 + (next() % 2000) as f64 / 1000.0;
                let (c, s) = (turn.cos() / size, turn.sin() / size);
                let shift = |n: u64| (n % 4000) as f64 / 10.0 - 200.0;
                let mut data = [c, s, shift(next()), -s, c, shift(next())];
                if k == 0 {
                    data[2] = f64::NAN;
                }
                let map = Map::Affine(data);
                let (ow, oh) = (1 + (next() % 200) as usize, 1 + (next() % 200) as usize);
                let mut fast = Image::blank(Mode::RGBa, ow, oh);
                let mut slow = Image::blank(Mode::RGBa, ow, oh);
                // SAFETY: the processor has AVX2 (ready).
                unsafe { wide::each4(&src, &mut fast, map) };
                each::<4>(&src, &mut slow, map);
                assert_eq!(fast.data, slow.data, "{w}x{h}, {data:?}");
            }
        }
    }

    #[test]
    fn python_s_round_to_places() {
        assert_eq!(py_round(0.123_456_789_012_345_68, 15), 0.123456789012346);
        assert_eq!(py_round(-0.5, 15), -0.5);
        assert_eq!(py_round(6.123233995736766e-17, 15), 0.0);
        assert!(py_round(-6.123233995736766e-17, 15).is_sign_negative());
    }
}
