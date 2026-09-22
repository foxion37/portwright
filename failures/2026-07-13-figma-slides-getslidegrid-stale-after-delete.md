---
date: 2026-07-13
service: figma
service_version: "Figma Slides plugin API (figma.getSlideGrid), 2026-07"
status: active
distributable: true
---

# Figma Slides `getSlideGrid()` can retain deleted-slide references

## Failure (root cause)

After deleting a generated Figma Slides section, a later `figma.getSlideGrid()` call failed with:

```text
Error: in getSlideGrid: The node with id "3:36" does not exist
```

The deleted node was a previous generated slide. The current `SLIDE_GRID` node tree and screenshots still showed only the intended surviving rows and slides.

## Correct handling

For same-session post-deletion verification, inspect the actual `SLIDE_GRID` tree instead of calling `getSlideGrid()`:

```js
const gridNode = figma.currentPage.children.find(n => n.type === "SLIDE_GRID");
return gridNode.children.map(row => ({
  id: row.id,
  name: row.name,
  slides: row.children.map(slide => slide.id),
}));
```

Treat this as an inspection-cache issue unless the direct node tree or screenshots also show stale slides. Do not rebuild the slide grid blindly when the visible node tree is already correct.
