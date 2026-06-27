# mskmbk

かまぼこ風味Miniscript

Joe Stroutさんが作ったMiniscriptをPythonへ移植したやつ

MiniScript 公式サイト: [https://miniscript.org](https://miniscript.org) 公式リポジトリ: [https://github.com/JoeStrout/miniscript](https://github.com/JoeStrout/miniscript)

使い方
```
1. mskmbk.pyをダウンロードする
2. 使いたいpythonのファイルと同じディレクトリに置く
3. import mskmbkって使いたいpythonファイルの上に書く
4. YES!
```

構文
```
mskmbk.ms(実行するやつ, context=〇〇, timeout=秒数)
await mskmbk.ams(実行するやつ, context=〇〇, timeout=秒数)

実行するやつは変数でも"こういうやつ"でもどっちでも動くよ
contextとtimeoutについては下みてね
```

msとamsのちがい
```
msってのは通常通りのやつ
amsは非同期で動かせるやつ、discordのbotにおすすめ
```

contextとは
```
外部から変数を渡せる感じ
pythonでの辞書(json)をcontextに変数なりなんだったりで渡すと
miniscript内でcontext.〇〇形式で受け取れるようになる
```

timeoutとは
```
実行時間を制限できる
タイムアウトしたときは
python内でtryうんたら文で
except mskmbk.TimeoutMsException as e:
    print("タイムアウト:", e.message)
とかって書くとなんかできるらしいよ
```

注意事項
```
amsでinput関数を使うと処理が止まっちゃうので使わないでね
非同期(ms()のやつ)で実行してるときにwaitとかyield使うと処理が止まるから気をつけてね
```

FAQ
```
Q1. お前が書いたん?
A1. 下みろ

Q2. どんなことに使っていい?
A2. だいたいなんでも使っていいよ

Q3. アイデアもAI?
A3. アイデアは自分

Q4. どれくらい本家Miniscriptと似てるの?
A4. https://miniscript.org/files/MiniScript-Manual.pdf ここに書いてあるとおりの仕様を大体やってるはず、間違ってたらissuesへgo
```

ライセンス

MIT License（詳細は [LICENSE](https://github.com/kamabokoheika/mskmbk/blob/main/LICENSE) を参照）

---

102%AIに書かせました
