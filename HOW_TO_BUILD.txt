HOW TO BUILD CVs
================
Each CV directory contains .tex source files (EN + PT).
Run from the cv's directory:

  pdflatex <filename>.tex && pdflatex <filename>.tex && rm -f *.aux *.log *.out

Run pdflatex TWICE (resolves hyperref cross-references), then clean artifacts.

Example (from repo root):
  cd main && pdflatex main.tex && pdflatex main.tex && rm -f *.aux *.log *.out
