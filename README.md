# partsCADlib
partsCADlib website

## 概要

このサイトは、以下の公式リポジトリから3D CADモデルの非公式カタログを提供します：

- https://github.com/adafruit/Adafruit_CAD_Parts
- https://github.com/sparkfun/3D_Models
- https://github.com/m5stack/M5_Hardware (MIT)

画像とダウンロードファイルは、これらのリポジトリから直接取得されます。

サムネイル画像のうち「STEPから生成」と表記されているものは、STEPファイルから自動生成されたものです。

## サイトURL

https://mqce.github.io/partsCADlib/

## ジェネレータスクリプト

`tools/` ディレクトリには、このカタログサイトを生成するためのPythonスクリプトが含まれています：

- `tools/generate.py` - カタログページを生成するメインスクリプト
- `tools/render_thumbs.py` - STEPファイルからサムネイル画像をレンダリングするスクリプト
