// Gate 5 - every equation must parse under KaTeX in strict mode.
//
// Run standalone: reads a JSON array of {id, page, latex} from stdin, tries
// to render each with the same options the site's rehype-katex plugin uses,
// and writes {"errors": [...]} to stdout. Strict mode is what catches a
// LaTeX macro this book's math never needed and Marker never validated --
// fast-mode extraction with no LLM pass is exactly where that surfaces.
//
// Lives next to the Python gates rather than inside site/ because it is a
// verification tool, not part of the shipped site, even though it borrows
// the site's own KaTeX install to render with.
import katex from "../site/node_modules/katex/dist/katex.mjs";

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = JSON.parse(Buffer.concat(chunks).toString("utf-8") || "[]");

  const errors = [];
  for (const item of input) {
    const latex = item.latex || "";
    try {
      katex.renderToString(latex, {
        displayMode: true,
        throwOnError: true,
        strict: "error",
        trust: false,
      });
    } catch (err) {
      errors.push({
        id: item.id,
        page: item.page,
        latex,
        message: err && err.message ? err.message : String(err),
      });
    }
  }

  process.stdout.write(JSON.stringify({ errors }));
  process.exit(errors.length ? 1 : 0);
}

main();
