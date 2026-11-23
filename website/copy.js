// The "Copy" buttons next to a prompt for a coding agent: copy the <code> beside the button, say "Copied".
(function () {
  "use strict";
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".agent-copy");
    if (!btn) return;
    const code = btn.parentElement.querySelector("code");
    const done = (ok) => {
      btn.textContent = ok ? "Copied" : "Select";
      btn.classList.toggle("done", ok);
      setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("done"); }, 2000);
    };
    const select = () => {  // no clipboard (an insecure page, a refusal): select the text for Ctrl+C
      const r = document.createRange();
      r.selectNodeContents(code);
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(r);
      done(false);
    };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(code.textContent).then(() => done(true), select);
    else select();
  });
})();
