import { describe, it, expect } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { App } from "./App.js";

describe("App", () => {
  it("renders through the project-local React dependencies", () => {
    const el = React.createElement(App);
    expect(renderToStaticMarkup(el)).toContain("Hello DINAS");
  });
});
