"""JEV P1 實驗：semantic-acceptance-judge benchmark（issue #42）。

實驗性子套件，刻意不接入 engine／pilot／report：主 ``patchmud`` 指令不依賴
這裡的任何模組，拔掉 JEV 後既有功能不受影響。入口為
``python -m patchmud.judge``，題庫與結果位於 ``experiments/jev-p1/``。
"""
