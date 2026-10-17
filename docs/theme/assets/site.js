// Local substring search also works for Chinese; no remote service or user tracking.
(() => {
  const input = document.getElementById("doc-search");
  if (!input) return;
  const results = document.getElementById("search-results");
  const status = document.getElementById("search-status");
  const nav = document.getElementById("doc-nav");
  const root = new URL(document.body.dataset.root.replace(/\/?$/, "/"), location.href);
  let index;
  let sequence = 0;
  input.addEventListener("input", async () => {
    const current = ++sequence;
    const query = input.value.trim().toLocaleLowerCase();
    results.replaceChildren();
    results.hidden = !query;
    nav.hidden = Boolean(query);
    status.textContent = query ? "正在搜索…" : "";
    if (!query) return;
    try {
      index ??= fetch(new URL("search/search_index.json", root)).then(response => {
        if (!response.ok) throw new Error("unavailable");
        return response.json();
      }).catch(error => { index = undefined; throw error; });
      const data = await index;
      if (current !== sequence) return;
      const matches = data.docs.filter(doc =>
        (doc.title + " " + doc.text).toLocaleLowerCase().includes(query)
      ).slice(0, 15);
      status.textContent = matches.length ? `显示 ${matches.length} 条结果（最多 15 条）` : "没有匹配结果";
      for (const doc of matches) {
        const target = new URL(doc.location, root);
        if (target.origin !== location.origin || !target.pathname.startsWith(root.pathname)) continue;
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = target.href;
        link.textContent = doc.title;
        item.append(link);
        results.append(item);
      }
    } catch {
      if (current === sequence) status.textContent = "搜索暂不可用，请使用下方导航";
      nav.hidden = false;
    }
  });
})();
