// Shared Typst setup for Study Buddy academic study guides.
// The Python renderer appends validated document content below this block.

#set page(
  paper: "a4",
  margin: (x: 18mm, y: 16mm),
  numbering: "1",
)

#set text(
  font: "New Computer Modern",
  size: 10pt,
  lang: "en",
)

#set par(
  leading: 0.58em,
  spacing: 0.72em,
  justify: true,
)

#show heading: it => block(
  above: 1.0em,
  below: 0.35em,
)[
  #text(fill: rgb("#1f5f6b"), weight: "bold")[#it.body]
]

#let callout(title, body) = block(
  fill: rgb("#eef5f4"),
  stroke: (left: 2pt + rgb("#1f5f6b")),
  inset: 7pt,
  radius: 2pt,
  width: 100%,
)[
  #text(weight: "bold", fill: rgb("#1f5f6b"))[#title]
  #v(3pt)
  #body
]

#let formula-box(body) = block(
  fill: rgb("#f7faf9"),
  stroke: rgb("#c8d8d6"),
  inset: 6pt,
  radius: 2pt,
  width: 100%,
)[
  #body
]
