# ハバネロの蔵 (www.pacman-frog.com)

WordPress から変換した静的サイトです。GitHub Pages で公開します。

## 構成
- `index.html` … トップ(「ようこそ！ハバネロの蔵へ」＋新着15件)
- `post-N/` … 記事(旧WordPressと同じURL)
- `page-N/`, `horned-frog/` … 固定ページ
- `category/○○/` … カテゴリ別一覧 / `archive/` … 年別の全記事一覧 / `search/` … サイト内検索
- `wp-content/uploads/` … 画像(旧サイトと同じパス)
- `assets/style.css` … デザイン(色・文字サイズはここで変更)
- `CNAME` … 独自ドメイン設定(GitHub Pagesが使う。消さない)

## 記事を追加するには
1. Claudeに「ハバネロの蔵に記事を追加して。文章は〜、画像は添付」と頼む(推奨)。
2. 自分でやる場合は、既存の `post-○○/index.html` をコピーして新しいフォルダ(例 `post-5000/`)に置き、タイトル・日付・本文を書き換える。画像は `wp-content/uploads/` に置く。
3. 新記事を `archive/index.html` と `search-index.json` にも追加する(Claudeに頼むと自動)。

## 注意
- 旧サイトのコメントは「承認済み」のみ表示。新しいコメントは受け付けない。コメント内のメールアドレスは非表示にしてある。
- 広告・アフィリエイトのコードは全て除去済み。
- 大文字小文字だけが違う画像名12組は、Windowsで扱えるよう `_v1` を付けた名前に変更済み。
- `_tools/build.py` は変換に使ったスクリプト(参考用。通常は不要)。
- 元のWordPressエクスポートXML(コメント投稿者のメールアドレスを含む)は、このリポジトリに入れないこと。
