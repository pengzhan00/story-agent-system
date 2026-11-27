"use strict";
(async () => {
  const boot = await fetch("/__launcher/boot", { cache: "no-store" }).then((r) => r.json());
  const select = document.getElementById("locale");
  let locale = localStorage.getItem("storyagent.locale") || (navigator.language.startsWith("zh") ? "zh-CN" : "en");
  let state = "stopped";
  let busy = false;
  const manage = location.pathname === "/__launcher/";
  let installed = boot.login_installed;
  const t = (key) => (boot.locales[locale] || boot.locales.en)[key] || key;
  const paint = () => {
    document.documentElement.lang = locale;
    document.title = t("gateway.page_title");
    select.value = locale;
    select.setAttribute("aria-label", t("gateway.language"));
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    document.getElementById("badge").textContent = t(`gateway.state.${state}`);
    document.getElementById("state").textContent = t(manage && state === "ready" ? "gateway.manage_ready" : `gateway.detail.${state}`);
    document.querySelector('[data-i18n="gateway.description"]').textContent = t(manage ? "gateway.manage_description" : "gateway.description");
    document.querySelector("h1").textContent = t(manage ? "gateway.manage_title" : "gateway.title");
    document.getElementById("start").disabled = busy || ["starting", "stopping", "ready"].includes(state);
    document.getElementById("start").hidden = manage && state === "ready";
    document.getElementById("stop").hidden = !manage || !["ready", "stopping"].includes(state);
    document.getElementById("stop").disabled = busy || state === "stopping";
    document.getElementById("install-login").hidden = !manage || !boot.login_supported || state !== "ready";
    document.getElementById("install-login").disabled = busy || installed;
    document.getElementById("login-state").textContent = manage && installed ? t("gateway.login_installed") : "";
    document.getElementById("return").hidden = !["ready", "stopping"].includes(state);
  };
  select.addEventListener("change", () => {
    locale = select.value;
    localStorage.setItem("storyagent.locale", locale);
    paint();
  });
  const poll = async () => {
    try {
      const r = await fetch("/__launcher/status", { cache: "no-store" });
      if (!r.ok) throw new Error();
      state = (await r.json()).state;
      paint();
      if (state === "ready" && !manage) { location.reload(); return; }
    } catch { document.getElementById("error").textContent = t("gateway.disconnected"); }
    setTimeout(poll, 1500);
  };
  document.getElementById("start").addEventListener("click", async () => {
    busy = true;
    document.getElementById("error").textContent = "";
    paint();
    try {
      const r = await fetch("/__launcher/start", { method: "POST", headers: { "X-StoryAgent-Launch": boot.token } });
      if (!r.ok) throw new Error();
      state = (await r.json()).state;
    } catch { document.getElementById("error").textContent = t("gateway.start_failed"); }
    finally { busy = false; paint(); }
  });
  document.getElementById("stop").addEventListener("click", async () => {
    if (!confirm(t("gateway.stop_confirm"))) return;
    busy = true;
    document.getElementById("error").textContent = "";
    paint();
    try {
      const r = await fetch("/__launcher/stop", { method: "POST", headers: { "X-StoryAgent-Launch": boot.token } });
      if (!r.ok) {
        document.getElementById("error").textContent = t([401, 403].includes(r.status) ? "gateway.stop_denied" : "gateway.stop_failed");
      } else { state = (await r.json()).state; }
    } catch { document.getElementById("error").textContent = t("gateway.stop_failed"); }
    finally { busy = false; paint(); }
  });
  document.getElementById("install-login").addEventListener("click", async () => {
    if (!confirm(t("gateway.install_confirm"))) return;
    busy = true; paint();
    try {
      const r = await fetch("/__launcher/install-login", { method: "POST", headers: { "X-StoryAgent-Launch": boot.token } });
      if (!r.ok) throw new Error();
      installed = true;
    } catch { document.getElementById("error").textContent = t("gateway.install_failed"); }
    finally { busy = false; paint(); }
  });
  paint();
  await poll();
})();
