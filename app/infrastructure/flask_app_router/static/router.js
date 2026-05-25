const PARTIAL_HEADER = "X-Flask-Router";
const CURRENT_PATH_HEADER = "X-Flask-Current-Path";
const CURRENT_TREE_HEADER = "X-Flask-Current-Tree";
const STATE_PATH_META = 'meta[name="flask-app-router-path"]';
const STATE_TREE_META = 'meta[name="flask-app-router-tree"]';

function state() {
  const path = document.querySelector(STATE_PATH_META)?.getAttribute("content");
  const tree = document.querySelector(STATE_TREE_META)?.getAttribute("content");
  return {
    path: path || `${window.location.pathname}${window.location.search}`,
    tree: tree ? tree.split(",").filter(Boolean) : ["root"],
  };
}

function setState(path, tree) {
  upsertMeta("flask-app-router-path", path);
  upsertMeta("flask-app-router-tree", tree.join(","));
}

function upsertMeta(name, content) {
  let element = document.querySelector(`meta[name="${CSS.escape(name)}"]`);
  if (!element) {
    element = document.createElement("meta");
    element.setAttribute("name", name);
    document.head.appendChild(element);
  }
  element.setAttribute("content", content);
}

function isInternalNavigation(event, anchor) {
  if (event.defaultPrevented || event.button !== 0) return false;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
  if (!anchor || anchor.target || anchor.hasAttribute("download")) return false;
  if (anchor.dataset.routerReload === "true") return false;

  const url = new URL(anchor.href, window.location.href);
  if (url.origin !== window.location.origin) return false;
  if (url.pathname === window.location.pathname && url.search === window.location.search) {
    return url.hash === "";
  }
  return true;
}

async function navigate(url, { push = true } = {}) {
  const current = state();
  let response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers: {
        [PARTIAL_HEADER]: "partial",
        [CURRENT_PATH_HEADER]: current.path,
        [CURRENT_TREE_HEADER]: current.tree.join(","),
        Accept: "application/json",
      },
      credentials: "same-origin",
      redirect: "manual",
    });
  } catch {
    window.location.assign(url);
    return;
  }

  if (!response.ok || !response.headers.get("content-type")?.includes("application/json")) {
    window.location.assign(url);
    return;
  }

  const payload = await response.json();
  if (payload.mode === "reload") {
    window.location.assign(payload.url || url);
    return;
  }
  if (payload.mode === "redirect") {
    window.location.assign(payload.url || url);
    return;
  }
  if (payload.mode !== "patch") {
    window.location.assign(url);
    return;
  }

  const boundary = document.querySelector(
    `[data-router-boundary="${CSS.escape(payload.boundary)}"]`,
  );
  if (!boundary) {
    window.location.assign(payload.url || url);
    return;
  }

  await loadStyles(payload.styles || []);
  boundary.innerHTML = payload.html || "";
  await loadScripts(payload.scripts || []);
  updateMetadata(payload.meta || {});
  setState(payload.url || url, payload.tree || ["root"]);

  if (push) {
    window.history.pushState({ flaskAppRouter: true }, "", payload.url || url);
  }
}

function updateMetadata(meta) {
  if (meta.title) document.title = meta.title;
  if (meta.description) upsertMeta("description", meta.description);
  if (meta.image) upsertProperty("og:image", meta.image);
}

function upsertProperty(property, content) {
  let element = document.querySelector(`meta[property="${CSS.escape(property)}"]`);
  if (!element) {
    element = document.createElement("meta");
    element.setAttribute("property", property);
    document.head.appendChild(element);
  }
  element.setAttribute("content", content);
}

async function loadStyles(styles) {
  await Promise.all(
    styles.map((href) => {
      if (document.querySelector(`link[rel="stylesheet"][href="${CSS.escape(href)}"]`)) {
        return Promise.resolve();
      }
      return new Promise((resolve, reject) => {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = href;
        link.onload = resolve;
        link.onerror = reject;
        document.head.appendChild(link);
      });
    }),
  );
}

async function loadScripts(scripts) {
  for (const src of scripts) {
    if (document.querySelector(`script[data-router-page-script][src="${CSS.escape(src)}"]`)) {
      continue;
    }
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.type = "module";
      script.src = src;
      script.dataset.routerPageScript = "true";
      script.onload = resolve;
      script.onerror = reject;
      document.body.appendChild(script);
    });
  }
}

document.addEventListener("click", (event) => {
  const anchor = event.target.closest?.("a[href]");
  if (!isInternalNavigation(event, anchor)) return;
  event.preventDefault();
  navigate(anchor.href);
});

window.addEventListener("popstate", () => {
  navigate(`${window.location.pathname}${window.location.search}`, { push: false });
});
