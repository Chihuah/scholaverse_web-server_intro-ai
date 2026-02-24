/* ============================================
   Scholaverse - App JavaScript
   HTMX event handlers, toast notifications
   Note: sidebar toggle is in base.html inline script
   ============================================ */

(function () {
  "use strict";

  /* --- Toast Notification System --- */
  function showToast(message, type, duration) {
    type = type || "info";
    duration = duration || 3000;

    var container = document.getElementById("toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "toast-container";
      container.style.cssText = "position:fixed;bottom:1rem;right:1rem;z-index:9999;display:flex;flex-direction:column;gap:0.5rem;";
      document.body.appendChild(container);
    }

    var toast = document.createElement("div");
    toast.style.cssText = [
      "font-family:'Noto Sans TC',sans-serif;font-size:12px;font-weight:700;",
      "padding:0.5rem 1rem;border-radius:4px;border:2px solid;",
      "transition:opacity 0.3s;",
      type === "success"
        ? "background:var(--rpg-bg-panel);border-color:var(--rpg-success);color:var(--rpg-success);"
        : type === "error" || type === "danger"
        ? "background:var(--rpg-bg-panel);border-color:var(--rpg-danger);color:var(--rpg-danger);"
        : "background:var(--rpg-bg-panel);border-color:var(--rpg-gold-dark);color:var(--rpg-text-primary);",
    ].join("");
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(function () {
      toast.style.opacity = "0";
      setTimeout(function () { toast.remove(); }, 300);
    }, duration);
  }

  window.showToast = showToast;

  /* --- HTMX Event Handlers --- */
  function initHTMX() {
    // Re-init Lucide icons after every HTMX swap
    document.body.addEventListener("htmx:afterSwap", function () {
      if (window.lucide) { lucide.createIcons(); }
    });

    // Show toast from HX-Trigger response header
    document.body.addEventListener("showToast", function (evt) {
      var detail = evt.detail || {};
      showToast(detail.message || "完成", detail.type || "success", detail.duration);
    });

    // Handle HTMX errors
    document.body.addEventListener("htmx:responseError", function (evt) {
      var status = evt.detail.xhr ? evt.detail.xhr.status : 0;
      if (status === 401 || status === 403) {
        showToast("權限不足", "error");
      } else if (status >= 500) {
        showToast("伺服器錯誤，請稍後再試", "error");
      } else {
        showToast("請求失敗", "warning");
      }
    });
  }

  /* --- Init on DOM Ready --- */
  document.addEventListener("DOMContentLoaded", function () {
    initHTMX();
  });
})();
