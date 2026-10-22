(function () {
  "use strict";

  const storageKey = `nix-darwin-test-report:tree-state:${window.location.pathname}`;
  let savedState = {};

  try {
    savedState = JSON.parse(window.sessionStorage.getItem(storageKey) || "{}");
  } catch (_error) {
    savedState = {};
  }

  function writeState(state) {
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify(state));
    } catch (_error) {
      // Report navigation still works if the browser refuses session storage.
    }
  }

  function setSummaryState(details) {
    const summary = details.querySelector("summary");
    if (summary) {
      summary.setAttribute("aria-expanded", details.open ? "true" : "false");
    }
  }

  document.querySelectorAll("[data-tree-id]").forEach((treeSection) => {
    const treeId = treeSection.getAttribute("data-tree-id") || "tree";

    treeSection.querySelectorAll("details.tree-node").forEach((details, index) => {
      const itemKey = `${treeId}:${index}`;

      if (Object.prototype.hasOwnProperty.call(savedState, itemKey)) {
        details.open = savedState[itemKey] === true;
      } else {
        details.open = false;
      }
      setSummaryState(details);

      details.addEventListener("toggle", () => {
        savedState[itemKey] = details.open;
        setSummaryState(details);
        writeState(savedState);
      });
    });
  });
})();
