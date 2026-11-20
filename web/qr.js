/**
 * Pure-JS ISO 18004 Byte-mode QR Code (Versions 1-6, ECC Level M) SVG generator.
 * Zero external CDN dependencies.
 */
(function (global) {
  "use strict";

  const GF_EXP = new Uint8Array(512);
  const GF_LOG = new Uint8Array(256);
  (function initGalois() {
    let x = 1;
    for (let i = 0; i < 255; i++) {
      GF_EXP[i] = x;
      GF_LOG[x] = i;
      x <<= 1;
      if (x & 0x100) x ^= 0x11d;
    }
    for (let i = 255; i < 512; i++) {
      GF_EXP[i] = GF_EXP[i - 255];
    }
  })();

  function gfMul(a, b) {
    if (a === 0 || b === 0) return 0;
    return GF_EXP[GF_LOG[a] + GF_LOG[b]];
  }

  function polyMul(p, q) {
    const res = new Uint8Array(p.length + q.length - 1);
    for (let i = 0; i < p.length; i++) {
      for (let j = 0; j < q.length; j++) {
        res[i + j] ^= gfMul(p[i], q[j]);
      }
    }
    return res;
  }

  function rsGeneratorPoly(ecLen) {
    let poly = new Uint8Array([1]);
    for (let i = 0; i < ecLen; i++) {
      poly = polyMul(poly, new Uint8Array([1, GF_EXP[i]]));
    }
    return poly;
  }

  function rsRemainder(data, ecLen) {
    const gen = rsGeneratorPoly(ecLen);
    const work = new Uint8Array(data.length + ecLen);
    work.set(data, 0);
    for (let i = 0; i < data.length; i++) {
      const factor = work[i];
      if (factor !== 0) {
        for (let j = 0; j < gen.length; j++) {
          work[i + j] ^= gfMul(gen[j], factor);
        }
      }
    }
    return work.slice(data.length);
  }

  // Versions 1-6 at ECC Level M (00), remainder bits = 0 for v1/v6, 7 for v2-v5
  const VERSIONS_M = [
    { ver: 1, size: 21, totalData: 16, blocks: 1, ecPerBlock: 10, remBits: 0, align: [] },
    { ver: 2, size: 25, totalData: 28, blocks: 1, ecPerBlock: 16, remBits: 7, align: [6, 18] },
    { ver: 3, size: 29, totalData: 44, blocks: 1, ecPerBlock: 26, remBits: 7, align: [6, 22] },
    { ver: 4, size: 33, totalData: 64, blocks: 2, ecPerBlock: 18, remBits: 7, align: [6, 26] },
    { ver: 5, size: 37, totalData: 86, blocks: 2, ecPerBlock: 24, remBits: 7, align: [6, 30] },
    { ver: 6, size: 41, totalData: 108, blocks: 4, ecPerBlock: 16, remBits: 0, align: [6, 34] },
  ];

  function encodeDataBits(bytes, spec) {
    const bits = [];
    function pushBits(val, len) {
      for (let i = len - 1; i >= 0; i--) {
        bits.push((val >>> i) & 1);
      }
    }
    // Mode 0100 (Byte), 8-bit count
    pushBits(0b0100, 4);
    pushBits(bytes.length, 8);
    for (let i = 0; i < bytes.length; i++) {
      pushBits(bytes[i], 8);
    }
    const maxBits = spec.totalData * 8;
    const termLen = Math.min(4, maxBits - bits.length);
    pushBits(0, termLen);
    while (bits.length % 8 !== 0) bits.push(0);

    const codewords = [];
    for (let i = 0; i < bits.length; i += 8) {
      let cw = 0;
      for (let j = 0; j < 8; j++) cw = (cw << 1) | bits[i + j];
      codewords.push(cw);
    }
    const padBytes = [0xec, 0x11];
    let pIdx = 0;
    while (codewords.length < spec.totalData) {
      codewords.push(padBytes[pIdx % 2]);
      pIdx++;
    }

    const blockDataLen = Math.floor(spec.totalData / spec.blocks);
    const dataBlocks = [];
    const ecBlocks = [];
    let offset = 0;
    for (let b = 0; b < spec.blocks; b++) {
      const slice = new Uint8Array(codewords.slice(offset, offset + blockDataLen));
      dataBlocks.push(slice);
      ecBlocks.push(rsRemainder(slice, spec.ecPerBlock));
      offset += blockDataLen;
    }

    const interleaved = [];
    for (let i = 0; i < blockDataLen; i++) {
      for (let b = 0; b < spec.blocks; b++) interleaved.push(dataBlocks[b][i]);
    }
    for (let i = 0; i < spec.ecPerBlock; i++) {
      for (let b = 0; b < spec.blocks; b++) interleaved.push(ecBlocks[b][i]);
    }

    const finalBits = [];
    for (const cw of interleaved) {
      for (let i = 7; i >= 0; i--) finalBits.push((cw >>> i) & 1);
    }
    for (let i = 0; i < spec.remBits; i++) finalBits.push(0);
    return finalBits;
  }

  function buildBaseMatrix(spec) {
    const n = spec.size;
    const mat = Array.from({ length: n }, () => new Int8Array(n)); // 0=empty, 1=white, 2=black
    const isFunc = Array.from({ length: n }, () => new Uint8Array(n));

    function setFunc(r, c, dark) {
      if (r >= 0 && r < n && c >= 0 && c < n) {
        mat[r][c] = dark ? 2 : 1;
        isFunc[r][c] = 1;
      }
    }

    function drawFinder(top, left) {
      for (let r = -1; r <= 7; r++) {
        for (let c = -1; c <= 7; c++) {
          const inBounds = top + r >= 0 && top + r < n && left + c >= 0 && left + c < n;
          if (!inBounds) continue;
          const dark =
            r >= 0 &&
            r <= 6 &&
            c >= 0 &&
            c <= 6 &&
            (r === 0 || r === 6 || c === 0 || c === 6 || (r >= 2 && r <= 4 && c >= 2 && c <= 4));
          setFunc(top + r, left + c, dark);
        }
      }
    }

    drawFinder(0, 0);
    drawFinder(0, n - 7);
    drawFinder(n - 7, 0);

    // Alignment patterns
    for (const ar of spec.align) {
      for (const ac of spec.align) {
        if (isFunc[ar][ac]) continue;
        for (let dr = -2; dr <= 2; dr++) {
          for (let dc = -2; dc <= 2; dc++) {
            const dark = Math.max(Math.abs(dr), Math.abs(dc)) !== 1;
            setFunc(ar + dr, ac + dc, dark);
          }
        }
      }
    }

    // Timing patterns
    for (let i = 8; i < n - 8; i++) {
      const dark = i % 2 === 0;
      if (!isFunc[6][i]) setFunc(6, i, dark);
      if (!isFunc[i][6]) setFunc(i, 6, dark);
    }

    // Dark module + format reservation
    setFunc(n - 8, 8, true);
    for (let i = 0; i < 9; i++) {
      if (!isFunc[8][i]) setFunc(8, i, false);
      if (!isFunc[i][8]) setFunc(i, 8, false);
    }
    for (let i = n - 8; i < n; i++) {
      if (!isFunc[8][i]) setFunc(8, i, false);
    }
    for (let i = n - 7; i < n; i++) {
      if (!isFunc[i][8]) setFunc(i, 8, false);
    }

    return { mat, isFunc };
  }

  function applyFormatInfo(mat, n, maskId) {
    // ECC Level M = 00 -> 5-bit data = (00 << 3) | maskId
    const data = maskId & 0x7;
    let rem = data << 10;
    const poly = 0x537;
    for (let i = 14; i >= 10; i--) {
      if ((rem >>> i) & 1) rem ^= poly << (i - 10);
    }
    const bits = ((data << 10) | rem) ^ 0x5412;

    const bitAt = (idx) => ((bits >>> idx) & 1) === 1;
    // Top-left & top-right/bottom-left format coordinates
    for (let i = 0; i <= 5; i++) mat[8][i] = bitAt(14 - i) ? 2 : 1;
    mat[8][7] = bitAt(8) ? 2 : 1;
    mat[8][8] = bitAt(7) ? 2 : 1;
    mat[7][8] = bitAt(6) ? 2 : 1;
    for (let i = 0; i <= 5; i++) mat[5 - i][8] = bitAt(5 - i) ? 2 : 1;

    for (let i = 0; i <= 6; i++) mat[n - 1 - i][8] = bitAt(14 - i) ? 2 : 1;
    for (let i = 0; i <= 7; i++) mat[8][n - 8 + i] = bitAt(7 - i) ? 2 : 1;
  }

  function maskCondition(maskId, r, c) {
    switch (maskId) {
      case 0: return (r + c) % 2 === 0;
      case 1: return r % 2 === 0;
      case 2: return c % 3 === 0;
      case 3: return (r + c) % 3 === 0;
      case 4: return (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0;
      case 5: return ((r * c) % 2) + ((r * c) % 3) === 0;
      case 6: return (((r * c) % 2) + ((r * c) % 3)) % 2 === 0;
      default: return (((r + c) % 2) + ((r * c) % 3)) % 2 === 0;
    }
  }

  function penaltyScore(mat, n) {
    let score = 0;
    const isDark = (r, c) => mat[r][c] === 2;
    for (let r = 0; r < n; r++) {
      let runLen = 1;
      for (let c = 1; c < n; c++) {
        if (isDark(r, c) === isDark(r, c - 1)) {
          runLen++;
          if (runLen === 5) score += 3;
          else if (runLen > 5) score += 1;
        } else {
          runLen = 1;
        }
      }
    }
    for (let c = 0; c < n; c++) {
      let runLen = 1;
      for (let r = 1; r < n; r++) {
        if (isDark(r, c) === isDark(r - 1, c)) {
          runLen++;
          if (runLen === 5) score += 3;
          else if (runLen > 5) score += 1;
        } else {
          runLen = 1;
        }
      }
    }
    for (let r = 0; r < n - 1; r++) {
      for (let c = 0; c < n - 1; c++) {
        const d = isDark(r, c);
        if (d === isDark(r, c + 1) && d === isDark(r + 1, c) && d === isDark(r + 1, c + 1)) {
          score += 3;
        }
      }
    }
    return score;
  }

  function generateMatrix(text) {
    const bytes = new TextEncoder().encode(text);
    const spec = VERSIONS_M.find((v) => bytes.length <= v.totalData - 2);
    if (!spec) {
      throw new Error("QR payload exceeds Version 6-M capacity.");
    }
    const bits = encodeDataBits(bytes, spec);
    const n = spec.size;
    let bestMat = null;
    let bestScore = Infinity;

    for (let maskId = 0; maskId < 8; maskId++) {
      const { mat, isFunc } = buildBaseMatrix(spec);
      let bitIdx = 0;
      let upward = true;
      for (let col = n - 1; col >= 1; col -= 2) {
        if (col === 6) col--;
        for (let step = 0; step < n; step++) {
          const r = upward ? n - 1 - step : step;
          for (let offset = 0; offset < 2; offset++) {
            const c = col - offset;
            if (!isFunc[r][c]) {
              const bit = bitIdx < bits.length ? bits[bitIdx++] === 1 : false;
              const dark = maskCondition(maskId, r, c) ? !bit : bit;
              mat[r][c] = dark ? 2 : 1;
            }
          }
        }
        upward = !upward;
      }
      applyFormatInfo(mat, n, maskId);
      const score = penaltyScore(mat, n);
      if (score < bestScore) {
        bestScore = score;
        bestMat = mat;
      }
    }
    return bestMat;
  }

  function renderQrSvg(text, sizePx) {
    const mat = generateMatrix(text);
    const n = mat.length;
    const quiet = 4;
    const viewSize = n + quiet * 2;
    const rects = [];
    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) {
        if (mat[r][c] === 2) {
          rects.push(`<rect x="${c + quiet}" y="${r + quiet}" width="1" height="1"/>`);
        }
      }
    }
    const px = sizePx || 180;
    return (
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${viewSize} ${viewSize}" ` +
      `width="${px}" height="${px}" shape-rendering="crispEdges" role="img" aria-label="Event QR Ticket">` +
      `<rect width="${viewSize}" height="${viewSize}" fill="#ffffff"/>` +
      `<g fill="#0f172a">${rects.join("")}</g></svg>`
    );
  }

  function renderQrPngDataUrl(text, sizePx) {
    const mat = generateMatrix(text);
    const n = mat.length;
    const quiet = 4;
    const total = n + quiet * 2;
    const targetPx = sizePx || 360;
    const scale = Math.max(4, Math.round(targetPx / total));
    const dim = total * scale;
    const canvas = document.createElement("canvas");
    canvas.width = dim;
    canvas.height = dim;
    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, dim, dim);
    ctx.fillStyle = "#0f172a";
    for (let r = 0; r < n; r++) {
      for (let c = 0; c < n; c++) {
        if (mat[r][c] === 2) {
          ctx.fillRect((c + quiet) * scale, (r + quiet) * scale, scale, scale);
        }
      }
    }
    return canvas.toDataURL("image/png");
  }

  // ISO 18004 Versions 1-6 block tables across ECC levels: 1=L, 0=M, 3=Q, 2=H
  const ECC_TABLES = {
    1: {
      21: [[19, 7]],
      25: [[34, 10]],
      29: [[55, 15]],
      33: [[80, 20]],
      37: [[108, 26]],
      41: [[68, 18], [68, 18]],
    },
    0: {
      21: [[16, 10]],
      25: [[28, 16]],
      29: [[44, 26]],
      33: [[32, 18], [32, 18]],
      37: [[43, 24], [43, 24]],
      41: [[27, 16], [27, 16], [27, 16], [27, 16]],
    },
    3: {
      21: [[13, 13]],
      25: [[22, 22]],
      29: [[17, 18], [17, 18]],
      33: [[24, 26], [24, 26]],
      37: [[15, 18], [15, 18], [16, 18], [16, 18]],
      41: [[19, 24], [19, 24], [19, 24], [19, 24]],
    },
    2: {
      21: [[9, 17]],
      25: [[16, 28]],
      29: [[13, 22], [13, 22]],
      33: [[9, 16], [9, 16], [9, 16], [9, 16]],
      37: [[11, 22], [11, 22], [12, 22], [12, 22]],
      41: [[15, 28], [15, 28], [15, 28], [15, 28]],
    },
  };

  function gfDiv(a, b) {
    if (a === 0) return 0;
    if (b === 0) throw new Error("GF division by zero");
    return GF_EXP[(GF_LOG[a] - GF_LOG[b] + 255) % 255];
  }

  function polyTrim(p) {
    let i = p.length - 1;
    while (i > 0 && p[i] === 0) i--;
    return p.slice(0, i + 1);
  }

  function polyAddLow(a, b) {
    const out = new Uint8Array(Math.max(a.length, b.length));
    for (let i = 0; i < a.length; i++) out[i] ^= a[i];
    for (let i = 0; i < b.length; i++) out[i] ^= b[i];
    return polyTrim(out);
  }

  function polyMulLow(a, b) {
    const out = new Uint8Array(a.length + b.length);
    for (let i = 0; i < a.length; i++) {
      if (a[i] === 0) continue;
      for (let j = 0; j < b.length; j++) {
        out[i + j] ^= gfMul(a[i], b[j]);
      }
    }
    return polyTrim(out);
  }

  function polyEvalLow(p, x) {
    let acc = 0;
    let xPow = 1;
    for (let i = 0; i < p.length; i++) {
      if (p[i]) acc ^= gfMul(p[i], xPow);
      xPow = gfMul(xPow, x);
    }
    return acc;
  }

  function rsDecodeBlock(blockBytes, dataLen, ecLen) {
    const n = blockBytes.length;
    const synd = new Uint8Array(ecLen);
    let anyErr = false;
    for (let j = 0; j < ecLen; j++) {
      let s = 0;
      const root = GF_EXP[j];
      for (let k = 0; k < n; k++) {
        s = blockBytes[k] ^ gfMul(s, root);
      }
      synd[j] = s;
      if (s !== 0) anyErr = true;
    }
    if (!anyErr) return blockBytes.slice(0, dataLen);

    // Euclidean RS decoder: synd(x) = sum_{j=0}^{ecLen-1} synd[j] * x^j
    let r0 = new Uint8Array(ecLen + 1);
    r0[ecLen] = 1;
    let r1 = polyTrim(synd);
    let t0 = new Uint8Array([0]);
    let t1 = new Uint8Array([1]);
    const halfEc = Math.floor(ecLen / 2);

    while (r1.length - 1 >= halfEc) {
      if (r1.length === 1 && r1[0] === 0) return null;
      const q = new Uint8Array(Math.max(1, r0.length - r1.length + 1));
      let rem = new Uint8Array(r0);
      const leadR1 = r1[r1.length - 1];
      while (rem.length >= r1.length && !(rem.length === 1 && rem[0] === 0)) {
        const shift = rem.length - r1.length;
        const scale = gfDiv(rem[rem.length - 1], leadR1);
        q[shift] ^= scale;
        for (let i = 0; i < r1.length; i++) {
          rem[i + shift] ^= gfMul(r1[i], scale);
        }
        rem = polyTrim(rem);
      }
      const nextT = polyAddLow(t0, polyMulLow(q, t1));
      r0 = r1;
      r1 = rem;
      t0 = t1;
      t1 = nextT;
    }

    const sigma0 = t1[0];
    if (!sigma0) return null;
    const invSigma0 = gfDiv(1, sigma0);
    const sigma = t1.map((v) => gfMul(v, invSigma0));
    const omega = r1.map((v) => gfMul(v, invSigma0));

    // Formal derivative of sigma in GF(2^8): odd indices shift down, even vanish
    const sigmaPrime = new Uint8Array(Math.max(1, sigma.length - 1));
    for (let i = 1; i < sigma.length; i += 2) {
      sigmaPrime[i - 1] = sigma[i];
    }

    const corrected = new Uint8Array(blockBytes);
    let errsFound = 0;
    const degSigma = sigma.length - 1;
    if (degSigma > halfEc) return null;
    for (let p = 0; p < n; p++) {
      const invX = GF_EXP[(255 - (p % 255)) % 255];
      if (polyEvalLow(sigma, invX) === 0) {
        const denom = polyEvalLow(sigmaPrime, invX);
        if (!denom) return null;
        const mag = gfMul(GF_EXP[p % 255], gfDiv(polyEvalLow(omega, invX), denom));
        corrected[n - 1 - p] ^= mag;
        errsFound++;
      }
    }
    if (errsFound !== degSigma) return null;

    for (let j = 0; j < ecLen; j++) {
      let s = 0;
      const root = GF_EXP[j];
      for (let k = 0; k < n; k++) {
        s = corrected[k] ^ gfMul(s, root);
      }
      if (s !== 0) return null;
    }
    return corrected.slice(0, dataLen);
  }

  const VALID_FORMAT_CODES = (function buildFormatTable() {
    const list = [];
    const poly = 0x537;
    for (let fmt = 0; fmt < 32; fmt++) {
      let rem = fmt << 10;
      for (let i = 14; i >= 10; i--) {
        if ((rem >>> i) & 1) rem ^= poly << (i - 10);
      }
      list.push({
        fmt,
        ecc: (fmt >>> 3) & 0x3,
        mask: fmt & 0x7,
        code: ((fmt << 10) | rem) ^ 0x5412,
      });
    }
    return list;
  })();

  function popCount(v) {
    let c = 0;
    let x = v;
    while (x) {
      x &= x - 1;
      c++;
    }
    return c;
  }

  function parseFormatCode(bits15) {
    let best = null;
    let bestDist = 4;
    for (const entry of VALID_FORMAT_CODES) {
      const dist = popCount(bits15 ^ entry.code);
      if (dist < bestDist) {
        bestDist = dist;
        best = entry;
      }
    }
    return best;
  }

  function decodeMatrixRaw(darkFn, n) {
    const spec = VERSIONS_M.find((v) => v.size === n);
    if (!spec) return null;

    // Copy A: top-left around finder (0..8)
    let fmtA = 0;
    const coordsA = [
      [8, 0], [8, 1], [8, 2], [8, 3], [8, 4], [8, 5], [8, 7], [8, 8],
      [7, 8], [5, 8], [4, 8], [3, 8], [2, 8], [1, 8], [0, 8],
    ];
    for (const [r, c] of coordsA) fmtA = (fmtA << 1) | (darkFn(r, c) ? 1 : 0);

    // Copy B: bottom-left col 8 (n-1..n-7) + top-right row 8 (n-8..n-1)
    let fmtB = 0;
    for (let i = 0; i <= 6; i++) fmtB = (fmtB << 1) | (darkFn(n - 1 - i, 8) ? 1 : 0);
    for (let i = 0; i <= 7; i++) fmtB = (fmtB << 1) | (darkFn(8, n - 8 + i) ? 1 : 0);

    const candidates = [parseFormatCode(fmtA), parseFormatCode(fmtB)].filter(Boolean);
    if (!candidates.length) return null;

    const tried = new Set();
    for (const fmtInfo of candidates) {
      const key = `${fmtInfo.ecc}:${fmtInfo.mask}`;
      if (tried.has(key)) continue;
      tried.add(key);

      const blocksSpec = ECC_TABLES[fmtInfo.ecc] && ECC_TABLES[fmtInfo.ecc][n];
      if (!blocksSpec) continue;

      const { isFunc } = buildBaseMatrix(spec);
      const rawBits = [];
      let upward = true;
      for (let col = n - 1; col >= 1; col -= 2) {
        if (col === 6) col--;
        for (let step = 0; step < n; step++) {
          const r = upward ? n - 1 - step : step;
          for (let offset = 0; offset < 2; offset++) {
            const c = col - offset;
            if (!isFunc[r][c]) {
              const bit = darkFn(r, c) ? 1 : 0;
              rawBits.push(maskCondition(fmtInfo.mask, r, c) ? bit ^ 1 : bit);
            }
          }
        }
        upward = !upward;
      }

      const codewords = [];
      for (let i = 0; i + 8 <= rawBits.length; i += 8) {
        let cw = 0;
        for (let j = 0; j < 8; j++) cw = (cw << 1) | rawBits[i + j];
        codewords.push(cw);
      }

      const blockCount = blocksSpec.length;
      const blocksData = blocksSpec.map(([d, e]) => new Uint8Array(d + e));
      const maxData = blocksSpec.reduce((m, [d]) => Math.max(m, d), 0);
      const maxEc = blocksSpec.reduce((m, [, e]) => Math.max(m, e), 0);
      let cwIdx = 0;
      for (let i = 0; i < maxData; i++) {
        for (let b = 0; b < blockCount; b++) {
          if (i < blocksSpec[b][0] && cwIdx < codewords.length) {
            blocksData[b][i] = codewords[cwIdx++];
          }
        }
      }
      for (let i = 0; i < maxEc; i++) {
        for (let b = 0; b < blockCount; b++) {
          const [d, e] = blocksSpec[b];
          if (i < e && cwIdx < codewords.length) {
            blocksData[b][d + i] = codewords[cwIdx++];
          }
        }
      }

      const allDataBytes = [];
      let rsOk = true;
      for (let b = 0; b < blockCount; b++) {
        const [d, e] = blocksSpec[b];
        const dec = rsDecodeBlock(blocksData[b], d, e);
        if (!dec) {
          rsOk = false;
          break;
        }
        for (let i = 0; i < dec.length; i++) allDataBytes.push(dec[i]);
      }
      if (!rsOk) continue;

      const dataBits = [];
      for (const byteVal of allDataBytes) {
        for (let i = 7; i >= 0; i--) dataBits.push((byteVal >>> i) & 1);
      }
      if (dataBits.length < 12) continue;

      let pos = 0;
      const readBits = (len) => {
        if (pos + len > dataBits.length) return -1;
        let v = 0;
        for (let i = 0; i < len; i++) v = (v << 1) | dataBits[pos++];
        return v;
      };

      const mode = readBits(4);
      if (mode === 0b0100) {
        const count = readBits(8);
        if (count < 0 || pos + count * 8 > dataBits.length) continue;
        const out = new Uint8Array(count);
        for (let i = 0; i < count; i++) out[i] = readBits(8);
        return new TextDecoder("utf-8").decode(out);
      }
    }
    return null;
  }

  function decodeMatrix(mat) {
    if (!Array.isArray(mat) || !mat.length) return null;
    const n = mat.length;
    if (!VERSIONS_M.some((v) => v.size === n)) return null;
    if (!mat.every((row) => row && row.length >= n)) return null;
    let hasTwos = false;
    for (let r = 0; r < n && !hasTwos; r++) {
      for (let c = 0; c < n; c++) {
        if (mat[r][c] === 2) {
          hasTwos = true;
          break;
        }
      }
    }
    const baseDark = (r, c) => (hasTwos ? mat[r][c] === 2 : !!mat[r][c]);
    const rotDark = (rot) => (r, c) => {
      if (rot === 0) return baseDark(r, c);
      if (rot === 1) return baseDark(n - 1 - c, r);
      if (rot === 2) return baseDark(n - 1 - r, n - 1 - c);
      return baseDark(c, n - 1 - r);
    };
    for (let rot = 0; rot < 4; rot++) {
      const decoded = decodeMatrixRaw(rotDark(rot), n);
      if (decoded !== null) return decoded;
    }
    return null;
  }

  function scoreFinderCorners(darkFn, n) {
    const origins = [
      [0, 0],
      [0, n - 7],
      [n - 7, 0],
    ];
    let hits = 0;
    let total = 0;
    for (const [top, left] of origins) {
      for (let r = 0; r < 7; r++) {
        for (let c = 0; c < 7; c++) {
          const expectDark =
            r === 0 || r === 6 || c === 0 || c === 6 || (r >= 2 && r <= 4 && c >= 2 && c <= 4);
          if (darkFn(top + r, left + c) === expectDark) hits++;
          total++;
        }
      }
    }
    return hits / total;
  }

  function locateRunCenters(grid, w, h, thresh) {
    const candidates = [];
    const step = Math.max(1, Math.floor(h / 180));
    const checkRun5 = (runs) => {
      const total = runs[0] + runs[1] + runs[2] + runs[3] + runs[4];
      if (total < 7) return 0;
      const u = total / 7;
      const maxVar = u * 0.55;
      if (
        Math.abs(runs[0] - u) < maxVar &&
        Math.abs(runs[1] - u) < maxVar &&
        Math.abs(runs[2] - 3 * u) < 3 * maxVar &&
        Math.abs(runs[3] - u) < maxVar &&
        Math.abs(runs[4] - u) < maxVar
      ) {
        return u;
      }
      return 0;
    };

    for (let y = 0; y < h; y += step) {
      const rowOff = y * w;
      const runs = [0, 0, 0, 0, 0];
      let state = 0;
      for (let x = 0; x < w; x++) {
        const isDark = grid[rowOff + x] < thresh;
        if (isDark) {
          if (state === 1 || state === 3) state++;
          runs[state]++;
        } else {
          if (state === 0 || state === 2) {
            state++;
            runs[state]++;
          } else if (state === 1 || state === 3) {
            runs[state]++;
          } else {
            const u = checkRun5(runs);
            if (u > 0) {
              const cx = x - runs[4] - runs[3] - runs[2] * 0.5;
              // Vertical confirmation at cx
              const ix = Math.max(0, Math.min(w - 1, Math.round(cx)));
              let top = y;
              while (top >= 0 && grid[top * w + ix] < thresh) top--;
              let bot = y;
              while (bot < h && grid[bot * w + ix] < thresh) bot++;
              const centerSpan = bot - top - 1;
              if (centerSpan > u * 1.4 && centerSpan < u * 5.0) {
                const cy = (top + 1 + bot - 1) * 0.5;
                candidates.push({ x: cx, y: cy, u });
              }
            }
            runs[0] = runs[2];
            runs[1] = runs[3];
            runs[2] = runs[4];
            runs[3] = 1;
            runs[4] = 0;
            state = 3;
          }
        }
      }
    }

    // Merge close hits into distinct finder clusters
    const clusters = [];
    for (const c of candidates) {
      let merged = false;
      for (const cl of clusters) {
        const dist = Math.hypot(cl.x / cl.count - c.x, cl.y / cl.count - c.y);
        if (dist < Math.max(8, c.u * 2.5)) {
          cl.x += c.x;
          cl.y += c.y;
          cl.u += c.u;
          cl.count++;
          merged = true;
          break;
        }
      }
      if (!merged) clusters.push({ x: c.x, y: c.y, u: c.u, count: 1 });
    }
    return clusters
      .map((cl) => ({ x: cl.x / cl.count, y: cl.y / cl.count, u: cl.u / cl.count, count: cl.count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 12);
  }

  function samplePointDark(grid, w, h, px, py, thresh) {
    const ix = Math.round(px);
    const iy = Math.round(py);
    let dark = 0;
    let samples = 0;
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const sx = ix + dx;
        const sy = iy + dy;
        if (sx >= 0 && sx < w && sy >= 0 && sy < h) {
          if (grid[sy * w + sx] < thresh) dark++;
          samples++;
        }
      }
    }
    return samples > 0 && dark * 2 >= samples;
  }

  function decodeImageData(imageData) {
    if (!imageData || !imageData.width || !imageData.height || !imageData.data) return null;
    const w = imageData.width;
    const h = imageData.height;
    const rgba = imageData.data;
    if (rgba.length < w * h * 4) return null;
    const gray = new Uint8Array(w * h);
    let minL = 255;
    let maxL = 0;
    for (let i = 0, p = 0; i < gray.length; i++, p += 4) {
      const a = rgba[p + 3];
      const lum =
        a < 128
          ? 255
          : Math.round(0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2]);
      gray[i] = lum;
      if (lum < minL) minL = lum;
      if (lum > maxL) maxL = lum;
    }
    if (maxL - minL < 24) return null;

    const thresholds = [
      minL + (maxL - minL) * 0.5,
      minL + (maxL - minL) * 0.42,
      minL + (maxL - minL) * 0.58,
    ];

    for (const thresh of thresholds) {
      // Strategy 1: Finder-pattern triangle localization (works on noisy camera frames & padded PNGs)
      const finders = locateRunCenters(gray, w, h, thresh);
      if (finders.length >= 3) {
        for (let i = 0; i < finders.length; i++) {
          for (let j = i + 1; j < finders.length; j++) {
            for (let k = j + 1; k < finders.length; k++) {
              const pts = [finders[i], finders[j], finders[k]];
              for (let topIdx = 0; topIdx < 3; topIdx++) {
                const tl = pts[topIdx];
                const p1 = pts[(topIdx + 1) % 3];
                const p2 = pts[(topIdx + 2) % 3];
                const d1 = Math.hypot(p1.x - tl.x, p1.y - tl.y);
                const d2 = Math.hypot(p2.x - tl.x, p2.y - tl.y);
                if (d1 < 20 || d2 < 20) continue;
                const ratio = d1 / d2;
                if (ratio < 0.78 || ratio > 1.28) continue;
                const dot = ((p1.x - tl.x) * (p2.x - tl.x) + (p1.y - tl.y) * (p2.y - tl.y)) / (d1 * d2);
                if (Math.abs(dot) > 0.3) continue;

                const cross = (p1.x - tl.x) * (p2.y - tl.y) - (p1.y - tl.y) * (p2.x - tl.x);
                const tr = cross > 0 ? p1 : p2;
                const bl = cross > 0 ? p2 : p1;
                const avgU = (tl.u + tr.u + bl.u) / 3;
                const estN = Math.round((d1 + d2) / (2 * avgU)) + 7;

                const sizes = VERSIONS_M.map((v) => v.size).sort(
                  (a, b) => Math.abs(a - estN) - Math.abs(b - estN)
                );
                for (const n of sizes.slice(0, 3)) {
                  const span = n - 7;
                  const mat = Array.from({ length: n }, (_, r) =>
                    Array.from({ length: n }, (__, c) => {
                      const u = (c - 3.0) / span;
                      const v = (r - 3.0) / span;
                      const sx = tl.x + u * (tr.x - tl.x) + v * (bl.x - tl.x);
                      const sy = tl.y + u * (tr.y - tl.y) + v * (bl.y - tl.y);
                      return samplePointDark(gray, w, h, sx, sy, thresh) ? 1 : 0;
                    })
                  );
                  const decoded = decodeMatrix(mat);
                  if (decoded !== null) return decoded;
                }
              }
            }
          }
        }
      }

      // Strategy 2: Tight dark bounding box (pixel-exact fallback for clean QR PNGs / cropped screenshots)
      let minX = w;
      let minY = h;
      let maxX = -1;
      let maxY = -1;
      for (let y = 0; y < h; y++) {
        const off = y * w;
        for (let x = 0; x < w; x++) {
          if (gray[off + x] < thresh) {
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
          }
        }
      }
      if (maxX > minX && maxY > minY) {
        const boxW = maxX - minX + 1;
        const boxH = maxY - minY + 1;
        if (Math.min(boxW, boxH) >= 21 && Math.abs(boxW - boxH) <= Math.max(8, boxW * 0.12)) {
          let bestN = null;
          let bestScore = 0;
          const buildBoxSampler = (n) => {
            const mw = boxW / n;
            const mh = boxH / n;
            return (r, c) =>
              samplePointDark(gray, w, h, minX + (c + 0.5) * mw, minY + (r + 0.5) * mh, thresh);
          };
          for (const spec of VERSIONS_M) {
            const sc = scoreFinderCorners(buildBoxSampler(spec.size), spec.size);
            if (sc > bestScore) {
              bestScore = sc;
              bestN = spec.size;
            }
          }
          if (bestN && bestScore >= 0.85) {
            const sampler = buildBoxSampler(bestN);
            const mat = Array.from({ length: bestN }, (_, r) =>
              Array.from({ length: bestN }, (__, c) => (sampler(r, c) ? 1 : 0))
            );
            const decoded = decodeMatrix(mat);
            if (decoded !== null) return decoded;
          }
        }
      }
    }
    return null;
  }

  global.CampusQR = {
    generateMatrix,
    renderQrSvg,
    renderQrPngDataUrl,
    decodeMatrix,
    decodeImageData,
  };
})(typeof window !== "undefined" ? window : globalThis);
