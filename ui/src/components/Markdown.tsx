import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useMemo, useRef } from "react";

marked.setOptions({ gfm: true, breaks: true });

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

export function Markdown({ text }: { text: string }) {
  const html = useMemo(() => DOMPurify.sanitize(marked.parse(text, { async: false }) as string), [text]);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Copy buttons on code blocks.
    ref.current?.querySelectorAll("pre").forEach((pre) => {
      if (pre.querySelector(".copy-code")) return;
      const b = document.createElement("button");
      b.className = "btn sm copy-code";
      b.textContent = "Copy";
      b.onclick = () => {
        navigator.clipboard.writeText(pre.querySelector("code")?.textContent ?? "");
        b.textContent = "Copied";
        setTimeout(() => (b.textContent = "Copy"), 1200);
      };
      pre.appendChild(b);
    });
  }, [html]);

  return <div ref={ref} className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}
