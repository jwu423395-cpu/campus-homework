// Official module examples: https://github.com/mathjax/MathJax-demos-node
// Raster API: https://github.com/thx/resvg-js#usage
import { mathjax } from 'mathjax-full/js/mathjax.js';
import { TeX } from 'mathjax-full/js/input/tex.js';
import { SVG } from 'mathjax-full/js/output/svg.js';
import { liteAdaptor } from 'mathjax-full/js/adaptors/liteAdaptor.js';
import { RegisterHTMLHandler } from 'mathjax-full/js/handlers/html.js';
import { AllPackages } from 'mathjax-full/js/input/tex/AllPackages.js';
import { Resvg } from '@resvg/resvg-js';
import { createHash } from 'node:crypto';

function handwrite(adaptor, svgNode, output, glyphs, tex) {
  let count = 0;
  function visit(node) {
    if (adaptor.kind(node) === 'path') {
      const code = Number.parseInt(adaptor.getAttribute(node, 'data-c') || '', 16);
      const original = Number.isFinite(code) ? String.fromCodePoint(code) : '';
      const char = original === '−' ? '-' : original.normalize('NFKC');
      const glyph = glyphs[char];
      const metrics = glyph && output.font.getChar('normal', code);
      if (glyph && metrics && metrics.slice(0, 3).every(Number.isFinite)) {
        let top = metrics[0] * 1000, bottom = -metrics[1] * 1000;
        // Operator metrics include extra TeX spacing; retain the actual axis/stroke height.
        if (char === '-') [bottom, top] = [230, 270];
        if (char === '=') [bottom, top] = [133, 367];
        const [xmin, ymin, xmax, ymax] = glyph.bounds;
        const seed = createHash('sha256').update(`${tex}:${count}:${code}`).digest();
        const sx = metrics[2] * 1000 / glyph.advance * (.94 + (seed[0] / 255 - .5) * .025);
        const sy = (top - bottom) / (ymax - ymin);
        const tx = (metrics[2] * 1000 - (xmax - xmin) * sx) / 2 - xmin * sx;
        const ty = bottom - ymin * sy;
        adaptor.setAttribute(node, 'd', glyph.path);
        const positioned = adaptor.getAttribute(node, 'transform') || '';
        adaptor.setAttribute(node, 'transform', `${positioned} matrix(${sx},0,0,${sy},${tx},${ty})`.trim());
        adaptor.setAttribute(node, 'data-handwriting', 'natural');
        count++;
      }
    }
    for (const child of adaptor.childNodes(node)) visit(child);
  }
  visit(svgNode);
  return count;
}

try {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  const adaptor = liteAdaptor();
  RegisterHTMLHandler(adaptor);
  const packages = AllPackages.filter(name => ['base', 'ams', 'newcommand', 'boldsymbol'].includes(name));
  const texInput = new TeX({ packages, formatError(_jax, error) { throw error; } });
  const output = new SVG({ fontCache: 'none' });
  const document = mathjax.document('', { InputJax: texInput, OutputJax: output });
  const rendered = (input.formulas || []).map(item => {
    const tex = item.tex;
    if (typeof tex !== 'string' || !tex.trim() || tex.length > 20000) throw new Error('TeX must contain 1–20000 characters.');
    if (/\\(?:require|href|url|html\w*|includegraphics|input|write|def\b|gdef\b)/.test(tex)) throw new Error('External resources and unsafe TeX commands are unsupported.');
    const size = Number(item.font_size || 36);
    const color = item.ink || '#20252b';
    if (!(size >= 10 && size <= 300) || !/^#[0-9a-f]{6}$/i.test(color)) throw new Error('Invalid font_size or ink.');
    const node = document.convert(tex, { display: true, em: size, ex: size / 2, containerWidth: 100000 });
    const svgNode = adaptor.firstChild(node);
    const box = adaptor.getAttribute(svgNode, 'viewBox').split(/\s+/).map(Number);
    if (box.length !== 4 || box.some(x => !Number.isFinite(x)) || box[2] <= 0 || box[3] <= 0) throw new Error('Invalid or empty formula bounds.');
    const padding = 50;
    adaptor.setAttribute(svgNode, 'viewBox', `${box[0] - padding} ${box[1] - padding} ${box[2] + 2 * padding} ${box[3] + 2 * padding}`);
    adaptor.setAttribute(svgNode, 'width', String(Math.ceil((box[2] + 2 * padding) * size / 1000)));
    adaptor.setAttribute(svgNode, 'height', String(Math.ceil((box[3] + 2 * padding) * size / 1000)));
    adaptor.setAttribute(svgNode, 'color', color);
    const handwrittenGlyphs = input.handwriting === 'natural'
      ? handwrite(adaptor, svgNode, output, input.handwriting_glyphs || {}, tex) : 0;
    const svg = adaptor.outerHTML(svgNode);
    if (/data-mml-node="merror"|data-mjx-error=/.test(svg)) throw new Error(`Invalid formula: ${tex}`);
    const result = new Resvg(svg, { font: {
      fontFiles: input.font_files || [], loadSystemFonts: false,
      defaultFontFamily: input.font_family || 'serif', serifFamily: input.font_family || 'serif',
    } }).render();
    return { tex, svg, png: result.asPng().toString('base64'), width: result.width, height: result.height, baseline: (-box[1] + padding) * size / 1000, handwritten_glyphs: handwrittenGlyphs };
  });
  process.stdout.write(JSON.stringify({ formulas: rendered, mathjax: '3.2.2', resvg: '2.6.2' }));
} catch (error) {
  process.stderr.write(`Formula rendering failed: ${error.message}\n`);
  process.exitCode = 1;
}
