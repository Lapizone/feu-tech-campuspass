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

  global.CampusQR = {
    generateMatrix,
    renderQrSvg,
  };
})(window);
