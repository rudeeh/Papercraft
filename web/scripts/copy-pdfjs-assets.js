/**
 * Postinstall script: copy pdf.js worker + cMaps + standard fonts
 * from node_modules into the Next.js public dir.
 *
 * These assets are NOT committed to git (they're ~2MB of binary files
 * that match the installed react-pdf version). This script runs
 * automatically after `npm install` so they're always present for
 * `npm run build` and `npm run dev`.
 */

const fs = require('fs');
const path = require('path');

const PUBLIC_DIR = path.join(process.cwd(), 'public');
const PDFJS_BUILD = path.join(process.cwd(), 'node_modules', 'pdfjs-dist', 'build');
const PDFJS_CMAPS = path.join(process.cwd(), 'node_modules', 'pdfjs-dist', 'cmaps');
const PDFJS_FONTS = path.join(process.cwd(), 'node_modules', 'pdfjs-dist', 'standard_fonts');

function ensureDir(dir) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function copyDir(src, dst) {
  ensureDir(dst);
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const dstPath = path.join(dst, entry.name);
    if (entry.isDirectory()) {
      copyDir(srcPath, dstPath);
    } else {
      fs.copyFileSync(srcPath, dstPath);
    }
  }
}

function copyWorker() {
  // react-pdf v10 uses .mjs format
  const candidates = [
    path.join(PDFJS_BUILD, 'pdf.worker.min.mjs'),
    path.join(PDFJS_BUILD, 'pdf.worker.min.js'),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      const dest = path.join(PUBLIC_DIR, 'pdf.worker.min.mjs');
      fs.copyFileSync(candidate, dest);
      console.log(`[copy-pdfjs-assets] worker: ${candidate} -> ${dest}`);
      return;
    }
  }
  console.warn('[copy-pdfjs-assets] WARNING: pdf.worker.min.{mjs,js} not found in node_modules');
}

function copyCmaps() {
  if (fs.existsSync(PDFJS_CMAPS)) {
    copyDir(PDFJS_CMAPS, path.join(PUBLIC_DIR, 'cmaps'));
    console.log('[copy-pdfjs-assets] cmaps copied');
  } else {
    console.warn('[copy-pdfjs-assets] WARNING: cmaps directory not found');
  }
}

function copyFonts() {
  if (fs.existsSync(PDFJS_FONTS)) {
    copyDir(PDFJS_FONTS, path.join(PUBLIC_DIR, 'fonts'));
    console.log('[copy-pdfjs-assets] standard fonts copied');
  } else {
    console.warn('[copy-pdfjs-assets] WARNING: standard_fonts directory not found');
  }
}

ensureDir(PUBLIC_DIR);
copyWorker();
copyCmaps();
copyFonts();
console.log('[copy-pdfjs-assets] done');
