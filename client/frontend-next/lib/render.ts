function esc(t: string) {
  return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Strip DSML / DeepSeek internal tags and XML tool tags */
export function sanitizeLLMOutput(text: string): string {
  // DeepSeek internal markers
  let t = text.replace(/<\|?\/?(?:DSML|tool_calls?|function_call|system|end|im_start|im_end|endoftext)[^>]*\|?>/g, "");
  // XML-like tool/observation tags
  t = t.replace(/<\/?(?:tool_call|function|result|observation|action|tool_result|assistant_response)[^>]*>/g, "");
  return t;
}

export function renderMsg(text: string): string {
  const mb: { d: boolean; m: string }[] = [];
  const cb: { lang: string; code: string }[] = [];
  let t = sanitizeLLMOutput(text);

  // Extract code blocks (```lang ... ```)
  t = t.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    cb.push({ lang: lang || "text", code: code.trimEnd() });
    return `%%CODE${cb.length - 1}%%`;
  });

  // Strip markdown headers → bold (outside code blocks)
  t = t.replace(/^(#{1,4})\s+(.+)$/gm, (_, _h, title) => `**${title.trim()}**\n`);

  // Extract math blocks
  t = t.replace(/\$\$([\s\S]+?)\$\$/g, (_, m) => { mb.push({ d: true, m }); return `%%M${mb.length - 1}%%`; });
  t = t.replace(/\$([^\$\n]+?)\$/g, (_, m) => { mb.push({ d: false, m }); return `%%M${mb.length - 1}%%`; });
  t = t.replace(/\\\[([\s\S]+?)\\\]/g, (_, m) => { mb.push({ d: true, m }); return `%%M${mb.length - 1}%%`; });
  t = t.replace(/\\\(([\s\S]+?)\\\)/g, (_, m) => { mb.push({ d: false, m }); return `%%M${mb.length - 1}%%`; });

  // Tables
  t = t.replace(/(\|.+\|[ \t]*\n?)+/g, (tbl) => {
    const rows = tbl.trim().split("\n").filter(r => r.includes("|"));
    if (rows.length < 2) return esc(tbl);
    const p = (r: string) => r.split("|").slice(1, -1).map(c => c.trim());
    const hdr = p(rows[0]);
    const sep = /^[\s|:\-]+$/.test(rows[1]) ? 2 : 1;
    let h = '<div class="msg-table-wrap"><table class="msg-table"><thead><tr>' +
      hdr.map(c => '<th>' + esc(c) + '</th>').join('') + '</tr></thead><tbody>';
    for (let i = sep; i < rows.length; i++) {
      const cells = p(rows[i]);
      if (!cells.length || /^[\s|:\-]+$/.test(rows[i])) continue;
      h += '<tr>' + cells.map(c => '<td>' + esc(c) + '</td>').join('') + '</tr>';
    }
    return h + '</tbody></table></div>';
  });

  t = t.replace(/<(?!\/?(?:table|thead|tbody|tr|th|td|div)[ >])/g, '&lt;');

  // Headers, bold, citations, inline code, lists
  // Headers already converted to bold above, skip legacy handler
  t = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/\[(\d+)\]/g, '<sup class="cite-num" data-n="$1">$1</sup>');
  t = t.replace(/\[来源(\d+)\]/g, '<sup class="cite-num" data-n="$1">$1</sup>');
  t = t.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');
  t = t.replace(/^[-•]\s+(.+)/gm, '<div class="msg-li">$1</div>');
  t = t.replace(/^(\d+)\.\s+(.+)/gm, '<div class="msg-oli"><span class="msg-oli-n">$1.</span> $2</div>');
  t = t.replace(/\n/g, '<br>');

  // Restore code blocks with Prism.js language classes
  t = t.replace(/%%CODE(\d+)%%/g, (_, i) => {
    const b = cb[+i];
    if (!b) return '';
    const id = `cb_${Date.now()}_${i}`;
    const langClass = `language-${b.lang}`;
    return `<div class="code-block">` +
      `<div class="code-header"><span class="code-lang">${esc(b.lang)}</span>` +
      `<button class="code-copy" onclick="navigator.clipboard?navigator.clipboard.writeText(document.getElementById('${id}').textContent).then(function(){this.textContent='✓ 已复制';var b=this;setTimeout(function(){b.textContent='复制'},1500)}.bind(this)):void 0">复制</button></div>` +
      `<pre class="code-pre"><code id="${id}" class="${langClass}">${esc(b.code)}</code></pre></div>`;
  });

  // Restore math — try to render with KaTeX directly (synchronous, reliable)
  t = t.replace(/%%M(\d+)%%/g, (_, i) => {
    const b = mb[+i];
    if (!b) return '';
    const w = typeof window !== 'undefined' ? (window as any) : null;
    if (w?.katex) {
      try {
        return w.katex.renderToString(b.m, { displayMode: b.d, throwOnError: false });
      } catch {}
    }
    // Fallback: leave as delimited text for doKatex() to pick up
    return b.d ? `<span class="math-block">$$${b.m}$$</span>` : `$${b.m}$`;
  });

  return t;
}

export function doKatex(el: HTMLElement) {
  const w = window as any;
  if (w.renderMathInElement) {
    try {
      w.renderMathInElement(el, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$", right: "$", display: false },
        ],
        throwOnError: false,
      });
    } catch {}
  }
  // Trigger Prism.js syntax highlighting
  if (w.Prism) {
    try { w.Prism.highlightAllUnder(el); } catch {}
  }
}

export function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 60000) return "刚刚";
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
  if (diff < 604800000) return `${Math.floor(diff / 86400000)} 天前`;
  return new Date(ts).toLocaleDateString();
}
