"""在真实 Chromium 里验证「写进 Quill 富文本编辑器」的那段 JS。

B 站新版投稿页的简介是 Quill 富文本（``.desc-container`` 里的 ``.ql-editor``），
真实表单只有在视频上传完成后才渲染，所以这里用一个**符合 Quill DOM 约定**的
替身页面来验证脚本本身：选择器能定位、``__quill`` 实例能被找到、
``setText(text, 'user')`` 的调用与读回校验都成立。

用法::

    python tests/bili_quill_js.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: 复刻 B 站投稿页简介区的结构：.desc-container > .ql-container(__quill) > .ql-editor
STUB_HTML = """
<!doctype html>
<html><body>
  <div class="desc-container">
    <div class="ql-container">
      <div class="ql-editor" contenteditable="true"></div>
    </div>
  </div>
  <script>
    // 极简 Quill 替身：只实现我们用到的那几个 API，行为对齐真实 Quill
    const root = document.querySelector('.ql-editor');
    const container = document.querySelector('.ql-container');
    let text = '';
    let lastSource = null;
    container.__quill = {
      getText: () => text,
      getLength: () => text.length + 1,
      setText: (value, source) => { text = value; lastSource = source; root.innerText = value; },
      setSelection: () => {},
      get lastSource() { return lastSource; },
    };
  </script>
</body></html>
"""

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}" + (f"　{detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


#: 复刻 B 站投稿页分区下拉的两栏结构：点一级分区后右侧列出该级的二级分区。
#: 一级/二级都记录了被点中的名字，用来断言「两级都点对了」。
DROPDOWN_HTML = """
<!doctype html>
<html><body>
  <div class="video-type">
    <div class="wrp">
      <div class="select-container">
        <div class="select-controller"><p class="select-item-cont">请选择分区</p></div>
      </div>
    </div>
  </div>
  <script>
    const TREE = {
      '生活': ['日常', '搞笑', '出行'],
      '知识': ['科学科普', '财经商业', '职业职场'],
      '游戏': ['单机游戏', '网络游戏'],
    };
    window.picked = {parent: null, child: null};
    const controller = document.querySelector('.select-controller');

    function close() {
      const old = document.querySelector('.drop-container');
      if (old) old.remove();
    }

    function open() {
      close();
      const box = document.createElement('div');
      box.className = 'drop-container';
      const fwrp = document.createElement('div');
      fwrp.className = 'drop-f-wrp';
      Object.keys(TREE).forEach((name) => {
        const item = document.createElement('div');
        item.className = 'drop-f-item';
        const p = document.createElement('p');
        p.className = 'f-item-content';
        p.innerText = name;
        item.appendChild(p);
        item.addEventListener('click', (event) => {
          event.stopPropagation();
          window.picked.parent = name;
          renderChildren(name);
        });
        fwrp.appendChild(item);
      });
      box.appendChild(fwrp);
      const twrp = document.createElement('div');
      twrp.className = 'drop-t-wrp';
      box.appendChild(twrp);
      document.body.appendChild(box);
    }

    function renderChildren(parent) {
      const wrp = document.querySelector('.drop-t-wrp');
      wrp.innerHTML = '';
      (TREE[parent] || []).forEach((name) => {
        const item = document.createElement('div');
        item.className = 'drop-t-item';
        const p = document.createElement('p');
        p.className = 'item-main';
        p.innerText = name;
        item.appendChild(p);
        item.addEventListener('click', (event) => {
          event.stopPropagation();
          window.picked.child = name;
          controller.querySelector('.select-item-cont').innerText = parent + ' → ' + name;
          close();
        });
        wrp.appendChild(item);
      });
    }

    controller.addEventListener('click', open);
  </script>
</body></html>
"""


#: 简介编辑区彻底找不到时的页面（模拟平台再次改版，选择器全部失效）
NO_EDITOR_HTML = "<!doctype html><html><body><div class=\"desc-container\"></div></body></html>"

#: 老版投稿页：简介是普通 textarea
LEGACY_TEXTAREA_HTML = '<!doctype html><html><body><textarea placeholder="简介"></textarea></body></html>'


#: 分区触发器在、点开却没有任何选项（模拟下拉没渲染出来）
EMPTY_DROPDOWN_HTML = """
<!doctype html><html><body>
  <div class="video-type"><div class="select-container">
    <div class="select-controller"><p class="select-item-cont">请选择分区</p></div>
  </div></div>
</body></html>
"""

#: 一级分区点得到，二级分区列表始终为空
HALF_DROPDOWN_HTML = """
<!doctype html><html><body>
  <div class="video-type"><div class="select-container">
    <div class="select-controller"><p class="select-item-cont">请选择分区</p></div>
  </div></div>
  <script>
    const c = document.querySelector('.select-controller');
    c.addEventListener('click', () => {
      if (document.querySelector('.drop-container')) return;
      const box = document.createElement('div');
      box.className = 'drop-container';
      ['生活', '知识'].forEach((n) => {
        const i = document.createElement('div');
        i.className = 'drop-f-item';
        i.innerText = n;
        i.addEventListener('click', (e) => e.stopPropagation());
        box.appendChild(i);
      });
      document.body.appendChild(box);
    });
  </script>
</body></html>
"""


def check_absent_editor(adapter) -> None:
    """简介编辑器找不到时，必须如实回报失败（调用方据此提醒用户手动补上）。

    ``_fill_description`` 的老版兜底以前用的是「找不到元素也算过关」的语义，
    于是简介一个字都没填进去也报成功，那句「简介未自动填写」的提醒永远不会出现，
    用户看到的还是「卡在内容备注那里」。
    """
    print("\n=== 简介编辑器缺失：必须如实回报失败 ===")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(NO_EDITOR_HTML)
        check("没有编辑器时 _fill_description 返回 False",
              adapter._fill_description(page, "一段简介") is False)
        browser.close()


def check_legacy_textarea(adapter) -> None:
    """老版 textarea 路径：填得进去要算成功，填不进去不能算成功。

    多行字段原先优先走「派发合成 ClipboardEvent」的粘贴路径，而合成事件只被富文本
    编辑器认账——原生 textarea 的 value 不受影响，可 ``dispatchEvent`` 本身一定「成功」。
    于是普通 textarea 上简介没写进去也回报填写成功。
    """
    from playwright.sync_api import sync_playwright

    print("\n=== 老版 textarea 简介：填得进去才算成功 ===")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(LEGACY_TEXTAREA_HTML)
        text = "老版投稿页的简介正文"
        ok = adapter._fill_description(page, text)
        check("老版 textarea 填写成功", ok is True, repr(ok))
        check("内容确实写进了 textarea",
              page.input_value("textarea") == text, repr(page.input_value("textarea")))
        browser.close()

def check_dropdown() -> None:
    """验证分区下拉的两级点选（老实现找的是根本不存在的搜索框，从没成功过）。"""
    from playwright.sync_api import sync_playwright

    from stent.platforms import bilibili as bili

    print("\n=== 分区下拉：一级 → 二级 ===")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(DROPDOWN_HTML)
        adapter = bili.BiliBiliAdapter()

        choice = adapter._select_partition(page, "知识", 0)
        picked = page.evaluate("() => window.picked")
        check("解析到分区", choice is not None, str(choice))
        check("点中了一级分区", picked["parent"] == "知识", str(picked))
        check("点中了二级分区", picked["child"] == bili.PARTITION_FALLBACK["知识"][1], str(picked))
        label = adapter._partition_label(page)
        check("下拉文案随之更新", "知识 → " in label, repr(label))

        # 已是目标分区时不该再点一次（避免把手动选择改掉）
        page.evaluate("() => { window.picked = {parent: null, child: null}; }")
        again = adapter._select_partition(page, "知识", 0)
        picked2 = page.evaluate("() => window.picked")
        check("已是目标分区时不重复点选", again is not None and picked2["parent"] is None, str(picked2))

        # 点不中就不能算成功：以前这里无条件返回解析结果，于是「下拉里一个选项都没有」
        # 「一级点得到但二级始终为空」这两种失败也会被回报成「分区已选」，
        # 调用方那条「分区未自动选择，请手动确认」的提醒永远不会出现。
        empty = browser.new_page()
        empty.set_content(EMPTY_DROPDOWN_HTML)
        check("下拉里没有任何选项时返回 None",
              adapter._select_partition(empty, "知识", 0) is None,
              repr(adapter._partition_label(empty)))

        half = browser.new_page()
        half.set_content(HALF_DROPDOWN_HTML)
        check("一级点得到、二级始终为空时返回 None",
              adapter._select_partition(half, "知识", 0) is None,
              repr(adapter._partition_label(half)))

        browser.close()


def main() -> int:
    from playwright.sync_api import sync_playwright

    from stent.platforms import bilibili as bili

    sample = "第一行简介\n\n第二行正文，带标点。"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(STUB_HTML)

        selector = bili.DESC_EDITOR_SELECTORS[0]
        print(f"主选择器：{selector}")

        check("选择器能定位到富文本编辑区", page.query_selector(selector) is not None)

        written = page.evaluate(bili.QUILL_SET_TEXT_JS, [selector, sample])
        check("脚本可执行且写入成功", bool(written), repr(written))

        got = page.evaluate(bili.QUILL_READ_JS, selector)
        check("读回内容与写入一致", got == sample, repr(got))

        source = page.evaluate("() => document.querySelector('.ql-container').__quill.lastSource")
        check("以 user 源写入（Vue 模型才收得到）", source == "user", repr(source))

        # 适配器自己的校验逻辑也要认这份内容
        check("适配器的读回校验通过", bili.BiliBiliAdapter._editor_has_text(page, sample, selector=selector))
        check("内容不符时校验必须失败",
              not bili.BiliBiliAdapter._editor_has_text(page, "完全不同的内容", selector=selector))

        # 选择器全部尝试一遍：第一个命中即返回，不会把内容写丢
        adapter = bili.BiliBiliAdapter()
        check("_fill_quill 走通", adapter._fill_quill(page, selector, "换一段文字"))
        check("_fill_description 走通", adapter._fill_description(page, "再换一段"))
        check("空简介直接算成功", adapter._fill_description(page, ""))

        browser.close()

    # 下面几组各自开浏览器：Playwright 同步 API 不能嵌套在已打开的上文里
    check_absent_editor(adapter)
    check_legacy_textarea(adapter)
    check_dropdown()

    print(f"\n===== B 站表单写入脚本验证：{PASS} 通过 / {FAIL} 失败 =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
