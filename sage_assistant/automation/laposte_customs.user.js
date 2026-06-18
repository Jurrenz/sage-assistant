// ==UserScript==
// @name         Sage Assistant - Douane La Poste
// @namespace    sage-assistant
// @version      0.1.0
// @description  Remplit la declaration douane La Poste depuis Sage Assistant.
// @match        https://www.laposte.fr/colissimo-en-ligne/parcours/douanes*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==

(function () {
  "use strict";

  const BRIDGE_URL = "http://127.0.0.1:8765/latest";
  const BUTTON_ID = "sage-assistant-laposte-fill";
  const PROMPT_STORAGE_KEY = "sageAssistantLaposteLastPromptKey";
  let lastPromptKey = "";

  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const norm = (text) => (text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const visible = (el) => !!(el && el.offsetParent !== null && !el.disabled);
  const byName = (name) => [...document.querySelectorAll(`[name="${name}"]`)].reverse().find(visible);

  function bridgeGet() {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: "GET",
        url: `${BRIDGE_URL}?t=${Date.now()}`,
        timeout: 2000,
        onload: (response) => {
          try {
            const body = JSON.parse(response.responseText || "{}");
            if (response.status >= 200 && response.status < 300 && body.ok) resolve(body.payload);
            else reject(new Error(body.error || `HTTP ${response.status}`));
          } catch (error) {
            reject(error);
          }
        },
        onerror: () => reject(new Error("Bridge Sage Assistant introuvable")),
        ontimeout: () => reject(new Error("Bridge Sage Assistant timeout")),
      });
    });
  }

  async function waitForName(name) {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const el = byName(name);
      if (el) return el;
      await wait(100);
    }
    throw new Error(`Champ introuvable: ${name}`);
  }

  function setValue(el, value) {
    if (!el) throw new Error("Champ introuvable");
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value")?.set;
    if (setter) setter.call(el, String(value));
    else el.value = String(value);
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: String(value) }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.blur();
  }

  function clickButton(text) {
    const wanted = norm(text);
    const button = [...document.querySelectorAll("button")].find((item) => visible(item) && norm(item.textContent).includes(wanted));
    if (!button) throw new Error(`Bouton introuvable: ${text}`);
    button.click();
  }

  async function fillOne(item, index) {
    if (index === 0) clickButton("Declarer un objet");
    else clickButton("Ajouter un article");
    await waitForName("description");
    setValue(byName("description"), item.description);
    setValue(await waitForName("originIso"), item.originIso);
    setValue(await waitForName("SHNumber"), item.hs);
    setValue(await waitForName("unitWeight"), item.unitWeight);
    setValue(await waitForName("unitValue"), item.unitValue);
    const qty = [...document.querySelectorAll('input[type="number"]')].reverse().find(visible);
    setValue(qty, item.quantity);
    await wait(100);
    clickButton("Enregistrer cet objet");
    await wait(350);
  }

  async function fillDeclaration(payload) {
    if (!payload || !Array.isArray(payload.items) || payload.items.length === 0) {
      throw new Error("Aucun article Sage Assistant a declarer");
    }
    if (!document.querySelector('[role="dialog"]')) clickButton("Commencer votre declaration");
    await wait(250);
    setValue(await waitForName("parcelContent"), payload.parcelContent || "envoi-commercial");
    for (let index = 0; index < payload.items.length; index += 1) {
      await fillOne(payload.items[index], index);
    }
  }

  async function fillFromBridge() {
    const payload = await bridgeGet();
    await fillDeclaration(payload);
    alert("Declaration remplie depuis Sage Assistant. Verifiez les totaux avant de continuer.");
  }

  function ensureButton() {
    if (document.getElementById(BUTTON_ID)) return;
    const button = document.createElement("button");
    button.id = BUTTON_ID;
    button.type = "button";
    button.textContent = "Remplir depuis Sage Assistant";
    button.style.cssText = [
      "position: fixed",
      "left: 18px",
      "bottom: 18px",
      "z-index: 2147483647",
      "background: #ffcb05",
      "color: #232323",
      "border: 1px solid #d7a900",
      "border-radius: 6px",
      "padding: 10px 14px",
      "font: 600 14px Arial, sans-serif",
      "box-shadow: 0 4px 16px rgba(0,0,0,.20)",
      "cursor: pointer",
    ].join(";");
    button.addEventListener("click", async () => {
      try {
        await fillFromBridge();
      } catch (error) {
        alert(`Sage Assistant: ${error.message}`);
      }
    });
    document.documentElement.appendChild(button);
  }

  async function maybePromptFill() {
    if (document.querySelector('[role="dialog"]')) return;
    const startButton = [...document.querySelectorAll("button")].find((item) => visible(item) && norm(item.textContent).includes("commencer votre declaration"));
    if (!startButton) return;
    let payload;
    try {
      payload = await bridgeGet();
    } catch (_error) {
      return;
    }
    const promptKey = `${payload.publishedAt || ""}:${payload.parcelName || ""}:${payload.totalWeightKg || ""}:${payload.totalValueHt || ""}`;
    const storedPromptKey = localStorage.getItem(PROMPT_STORAGE_KEY) || "";
    if (!promptKey || promptKey === lastPromptKey || promptKey === storedPromptKey) return;
    lastPromptKey = promptKey;
    localStorage.setItem(PROMPT_STORAGE_KEY, promptKey);
    if (confirm(`Sage Assistant a un colis pret (${payload.parcelName || "colis"}). Remplir la declaration maintenant ?`)) {
      await fillDeclaration(payload);
      alert("Declaration remplie depuis Sage Assistant. Verifiez les totaux avant de continuer.");
    }
  }

  ensureButton();
  setInterval(ensureButton, 2000);
  setTimeout(maybePromptFill, 800);
  setInterval(maybePromptFill, 3000);
})();
