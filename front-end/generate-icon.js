#!/usr/bin/env node
// Generates front-end/assets/icon.ico — run once, commit the output.
// Usage: node generate-icon.js
const fs   = require('fs');
const path = require('path');
const zlib = require('zlib');

const BG = [26, 26, 46];   // #1a1a2e dark navy
const FG = [80, 175, 76];  // #50AF4C aria green

function distToSeg(px, py, ax, ay, bx, by) {
    const dx = bx-ax, dy = by-ay;
    const len2 = dx*dx + dy*dy;
    if (len2 === 0) return Math.hypot(px-ax, py-ay);
    const t = Math.max(0, Math.min(1, ((px-ax)*dx + (py-ay)*dy) / len2));
    return Math.hypot(px-ax-t*dx, py-ay-t*dy);
}

function renderIcon(size) {
    const buf = Buffer.alloc(size * size * 4);
    for (let i = 0; i < size*size; i++) {
        buf[i*4]=BG[0]; buf[i*4+1]=BG[1]; buf[i*4+2]=BG[2]; buf[i*4+3]=255;
    }

    const sw  = Math.max(1.5, size * 0.115); // stroke width
    const m   = size * 0.12;                 // margin
    const ax  = size*0.5,  ay  = m;          // apex
    const lx  = m,         ly  = size-m;     // bottom-left
    const rx  = size-m,    ry  = size-m;     // bottom-right
    const barY  = size * 0.57;
    const barLx = ax + (barY-ay)/(ly-ay)*(lx-ax);
    const barRx = ax + (barY-ay)/(ry-ay)*(rx-ax);

    for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
            const cx = x+0.5, cy = y+0.5;
            const d = Math.min(
                distToSeg(cx, cy, ax, ay, lx, ly),
                distToSeg(cx, cy, ax, ay, rx, ry),
                distToSeg(cx, cy, barLx, barY, barRx, barY)
            );
            const alpha = Math.max(0, Math.min(1, sw*0.5 + 0.5 - d));
            if (alpha > 0) {
                const i = (y*size + x)*4;
                buf[i]   = Math.round(BG[0]*(1-alpha) + FG[0]*alpha);
                buf[i+1] = Math.round(BG[1]*(1-alpha) + FG[1]*alpha);
                buf[i+2] = Math.round(BG[2]*(1-alpha) + FG[2]*alpha);
                buf[i+3] = 255;
            }
        }
    }
    return buf;
}

// CRC32 for PNG chunks
const crcTable = (() => {
    const t = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
        let c = i;
        for (let j = 0; j < 8; j++) c = (c&1) ? (0xEDB88320 ^ (c>>>1)) : (c>>>1);
        t[i] = c;
    }
    return t;
})();

function crc32(buf) {
    let c = 0xFFFFFFFF;
    for (const b of buf) c = crcTable[(c^b)&0xFF] ^ (c>>>8);
    return (c ^ 0xFFFFFFFF) >>> 0;
}

function pngChunk(type, data) {
    const out = Buffer.alloc(12 + data.length);
    out.writeUInt32BE(data.length, 0);
    out.write(type, 4, 'ascii');
    data.copy(out, 8);
    out.writeUInt32BE(crc32(out.slice(4, 8+data.length)), 8+data.length);
    return out;
}

function toPNG(rgba, w, h) {
    const sig  = Buffer.from([137,80,78,71,13,10,26,10]);
    const ihdr = Buffer.alloc(13);
    ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4);
    ihdr[8]=8; ihdr[9]=6; // 8-bit RGBA
    const raw = Buffer.alloc(h*(1+w*4));
    for (let y = 0; y < h; y++) {
        raw[y*(1+w*4)] = 0; // filter byte: None
        rgba.copy(raw, y*(1+w*4)+1, y*w*4, (y+1)*w*4);
    }
    const idat = zlib.deflateSync(raw, { level: 6 });
    return Buffer.concat([sig, pngChunk('IHDR',ihdr), pngChunk('IDAT',idat), pngChunk('IEND',Buffer.alloc(0))]);
}

function toICO(images) {
    // images = [{w, h, data: Buffer(PNG)}]
    const n = images.length;
    const hdrSize = 6 + n*16;
    const hdr = Buffer.alloc(hdrSize);
    hdr.writeUInt16LE(0,0); hdr.writeUInt16LE(1,2); hdr.writeUInt16LE(n,4);
    let offset = hdrSize;
    images.forEach(({w,h,data}, i) => {
        const b = 6+i*16;
        hdr[b]   = w===256 ? 0 : w;
        hdr[b+1] = h===256 ? 0 : h;
        hdr[b+2] = 0; hdr[b+3] = 0;
        hdr.writeUInt16LE(1,  b+4);
        hdr.writeUInt16LE(32, b+6);
        hdr.writeUInt32LE(data.length, b+8);
        hdr.writeUInt32LE(offset,      b+12);
        offset += data.length;
    });
    return Buffer.concat([hdr, ...images.map(img => img.data)]);
}

const sizes  = [16, 32, 48, 256];
const images = sizes.map(s => ({ w:s, h:s, data: toPNG(renderIcon(s), s, s) }));

const outPath = path.join(__dirname, 'assets', 'icon.ico');
fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, toICO(images));
console.log('Generated', outPath);
