import string
import random
from pygments import highlight, token, lexer, styles
from pygments.formatters import get_formatter_by_name
from functools import partial
import panel as pn


class MlirLexer(lexer.RegexLexer):
    # This definition comes from a go clone of pygments
    # https://github.com/alecthomas/chroma/blob/master/lexers/m/mlir.go
    name = "MLIR"
    aliases = ["mlir"]
    filenames = ["*.mlir"]
    mimetypes = ["text/x-mlir"]

    # fmt: off
    tokens = {
        "root": [
            lexer.include("whitespace"),
            (r'c?"[^"]*?"', token.Literal.String),
            (r"\^([-a-zA-Z$._][\w\-$.0-9]*)\s*", token.Name.Label),
            (r"([\w\d_$.]+)\s*=", token.Name.Label),
            lexer.include("keyword"),
            (r"->", token.Punctuation),
            (r"@([\w_][\w\d_$.]*)", token.Name.Function),
            (r"[%#][\w\d_$.]+", token.Name.Variable),
            (r"([1-9?][\d?]*\s*x)+", token.Literal.Number),
            (r"0[xX][a-fA-F0-9]+", token.Literal.Number),
            (r"-?\d+(?:[.]\d+)?(?:[eE][-+]?\d+(?:[.]\d+)?)?", token.Literal.Number),
            (r"[=<>{}\[\]()*.,!:]|x\b", token.Punctuation),
            (r"[\w\d]+", token.Text),
        ],
        "whitespace": [
            (r"(\n|\s)+", token.Text),
            (r"//.*?\n", token.Comment)
        ],
        "keyword": [
            (lexer.words(("constant", "return")), token.Keyword.Type),
            (lexer.words(("func", "loc", "memref", "tensor", "vector")), token.Keyword.Type),
            (r"bf16|f16|f32|f64|index", token.Keyword),
            (r"i[1-9]\d*", token.Keyword),
        ],
    }
    # fmt: on


class Explorer:
    def __init__(self, debug_result):
        self.dr = debug_result
        self.panel = None
        self.rndchars = "".join(random.choice(string.ascii_letters) for _ in range(9))
        self.lexer = MlirLexer()
        self.embed = False
        self.show_line_numbers = True
        self.highlight_style = "default"
        self.html_formatter = None
        self._shown_stages = [0, 1, 0, 0, 1]
        self._code_blocks = [None, None, None, None, None]
        self.passes = [
            f"[{i+1}/{len(self.dr.passes)}] {p}" for i, p in enumerate(self.dr.passes)
        ]

    @property
    def initial_pass(self):
        return f"[0/{len(self.passes)}] Initial"

    def _find_pass_index(self, pass_name: str) -> int:
        return int(pass_name.split("/")[0][1:])

    def show(self, embed=False, initial_style=None):
        pn.extension()
        self.embed = embed

        # Apply initial styles
        if initial_style is None:
            initial_style = {}
        self.show_line_numbers = initial_style.get(
            "line_numbers", self.show_line_numbers
        )
        self.highlight_style = initial_style.get(
            "highlight_style", self.highlight_style
        )
        self.build()
        if "tab" in initial_style:
            tab_name = initial_style.get("tab", "sequential").lower()
            pass_name = initial_style.get("pass", self.initial_pass)
            ipass = self._find_pass_index(pass_name)
            if tab_name == "sequential":
                assert (
                    pass_name != self.initial_pass
                ), "Sequential tab does not have Initial option"
                self._ui["seq_select"].value = pass_name
                self._shown_stages[0] = ipass - 1
                self._shown_stages[1] = ipass
            elif tab_name == "single":
                self._ui["tabs"].active = 1
                self._ui["sgl_select"].value = pass_name
                self._shown_stages[2] = ipass
            elif tab_name == "double":
                self._ui["tabs"].active = 2
                pass2_name = initial_style.get("pass2", self.passes[0])
                ipass2 = self._find_pass_index(pass2_name)
                self._ui["dbl_select_left"].value = pass_name
                self._ui["dbl_select_right"].value = pass2_name
                self._shown_stages[3] = ipass
                self._shown_stages[4] = ipass2
            elif tab_name == "edit":
                self._ui["tabs"].active = 3
            else:
                raise KeyError(f"Invalid tab name: {initial_style.get('tab')}")
            self.rebuild()

        if embed:
            return self.panel
        else:
            pn.config.raw_css = [
                self.html_formatter.get_style_defs(f".highlight_{self.rndchars}"),
                """
                pre {
                  overflow-x: auto;
                  white-space: pre-wrap;
                  white-space: -moz-pre-wrap;
                  white-space: -pre-wrap;
                  white-space: -o-pre-wrap;
                  word-wrap: break-word;
                }
                """,
            ]
            return self.panel.show("MLIR Code Pass Explorer")

    def _uniquify_pass_names(self):
        """
        Checkbox groups and select options expect unique labels. Since passes can occur
        multiple times, we need to differentiate the labels. We do that by adding spaces
        to the pass names to differentiate them.
        """
        seen = set()
        unique_pass_names = []
        for p in self.dr.passes:
            p = "--" + p
            while p in seen:
                p = p + " "
            seen.add(p)
            unique_pass_names.append(p)
        return unique_pass_names

    def build(self):
        self.panel = pn.GridSpec(sizing_mode="stretch_width")

        ckbox_linenos = pn.widgets.Checkbox(
            name="Show Line Numbers", value=self.show_line_numbers, width=150
        )
        style_select = pn.widgets.Select(
            name="Highlighting Style",
            options=list(styles.get_all_styles()),
            value=self.highlight_style,
            width=150,
        )
        tabs = pn.Tabs()

        self.panel[0, 0] = pn.Column(
            pn.Row(
                pn.Spacer(width=270), ckbox_linenos, style_select, background="#EEEEFF"
            ),
            tabs,
        )

        # Sequential
        seq_select = pn.widgets.Select(name="Passes", options=self.passes, width=320)
        seq_btn_left = pn.widgets.Button(
            name="\u25c0", width=150, button_type="primary"
        )
        seq_btn_right = pn.widgets.Button(
            name="\u25b6", width=150, button_type="primary"
        )
        seq_code_left = pn.pane.HTML("Not initialized")
        seq_code_right = pn.pane.HTML("Not initialized")
        sequential = pn.GridSpec(sizing_mode="stretch_width")
        seq_code_row = pn.GridSpec(sizing_mode="stretch_width")
        seq_code_row[0, 0] = seq_code_left
        seq_code_row[0, 1] = seq_code_right
        sequential[0, 0] = pn.Column(
            seq_select,
            pn.Row(seq_btn_left, seq_btn_right),
            seq_code_row,
        )
        tabs.append(("Sequential", sequential))

        # Single
        sgl_select = pn.widgets.Select(
            name="Passes", options=[self.initial_pass] + self.passes, width=320
        )
        sgl_code = pn.pane.HTML("Not initialized")
        single = pn.GridSpec(sizing_mode="stretch_width")
        single[0, 0] = pn.Column(sgl_select, sgl_code)
        tabs.append(("Single", single))

        # Double
        dbl_select_left = pn.widgets.Select(
            name="Passes", options=[self.initial_pass] + self.passes
        )
        dbl_select_right = pn.widgets.Select(
            name="Passes",
            options=[self.initial_pass] + self.passes,
            value=self.passes[0],
        )
        dbl_code_left = pn.pane.HTML("Not initialized")
        dbl_code_right = pn.pane.HTML("Not initialized")
        double = pn.GridSpec(sizing_mode="stretch_width")
        double[0, 0] = pn.Column(dbl_select_left, dbl_code_left)
        double[0, 1] = pn.Column(dbl_select_right, dbl_code_right)
        tabs.append(("Double", double))

        # Edit
        instructions = pn.pane.Markdown(
            "Apply must be clicked to save changes."
            "When done editing MLIR, click **outside** the text box to enable the Apply button."
        )
        edit_text = pn.widgets.TextAreaInput(
            name="Edit Initial MLIR", value=self.dr.stages[0], min_height=800
        )
        ckbox_passes = pn.widgets.CheckBoxGroup(
            name="Passes",
            inline=False,
            options=self._uniquify_pass_names(),
            value=self._uniquify_pass_names(),
        )
        apply_button = pn.widgets.Button(
            name="Apply Changes", width=150, button_type="primary", disabled=True
        )
        edit = pn.GridSpec(sizing_mode="stretch_width")
        edit[0, 0] = pn.Column(instructions, apply_button, ckbox_passes, edit_text)
        tabs.append(("Edit", edit))

        # Link dynamic items
        ckbox_linenos.link(tabs, callbacks={"value": self.line_number_toggle})
        style_select.link(tabs, callbacks={"value": self.highlight_style_callback})
        sgl_select.link(sgl_code, callbacks={"value": self.code_callback})
        dbl_select_left.link(dbl_code_left, callbacks={"value": self.code_callback})
        dbl_select_right.link(dbl_code_right, callbacks={"value": self.code_callback})
        seq_select.link(
            seq_code_left, callbacks={"value": partial(self.code_callback, offset=-1)}
        )
        seq_select.link(seq_code_right, callbacks={"value": self.code_callback})
        seq_btn_left.link(seq_select, callbacks={"value": self.button_callback})
        seq_btn_right.link(seq_select, callbacks={"value": self.button_callback})
        edit_text.link(apply_button, callbacks={"value": self.enable_button})
        ckbox_passes.link(apply_button, callbacks={"value": self.enable_button})
        apply_button.link(tabs, callbacks={"value": self.apply_changes})

        self._ui = {
            "seq_select": seq_select,
            "sgl_select": sgl_select,
            "dbl_select_left": dbl_select_left,
            "dbl_select_right": dbl_select_right,
            "edit_text": edit_text,
            "ckbox_passes": ckbox_passes,
            "tabs": tabs,
        }
        self._code_blocks = [
            seq_code_left,
            seq_code_right,
            sgl_code,
            dbl_code_left,
            dbl_code_right,
        ]
        self.rebuild()

    def rebuild(self, index=None):
        formatter_kwargs = {"style": self.highlight_style}
        if self.show_line_numbers:
            formatter_kwargs["linenos"] = "inline"
        if self.embed:
            formatter_kwargs["noclasses"] = True
        else:
            formatter_kwargs["cssclass"] = f"highlight_{self.rndchars}"
        self.html_formatter = get_formatter_by_name("html", **formatter_kwargs)

        if index is None:
            for codeblock, stage in zip(self._code_blocks, self._shown_stages):
                codeblock.object = highlight(
                    self.dr.stages[stage], self.lexer, self.html_formatter
                )
        else:
            self._code_blocks[index].object = highlight(
                self.dr.stages[self._shown_stages[index]],
                self.lexer,
                self.html_formatter,
            )

    # Callbacks
    def line_number_toggle(self, target, event):
        self.show_line_numbers = not self.show_line_numbers
        self.rebuild()

    def highlight_style_callback(self, target, event):
        self.highlight_style = event.obj.value
        if self.embed:
            self.rebuild()
        else:
            formatter = get_formatter_by_name("html", style=self.highlight_style)
            new_styles = formatter.get_style_defs(
                f".highlight_{self.rndchars}"
            ).splitlines()
            # Must run javascript code to update the CSS in the DOM
            js_code = """
                <script>
                var new_styles = {{new_styles}};
                for (let i = 0; i < document.styleSheets.length; i++) {
                    var ss = document.styleSheets[i];
                    if (ss.cssRules[ss.cssRules.length - 1].selectorText.includes("{{rndchars}}") 
                        || ss.cssRules[0].selectorText.includes("{{rndchars}}")) {
                        var num_rules = ss.cssRules.length;
                        for (let j = 0; j < num_rules; j++) {
                            ss.deleteRule(0);
                        }
                        for (let j = 0; j < new_styles.length; j++) {
                            ss.insertRule(new_styles[j], 0);
                        }
                        break
                    }
                }
                </script>
            """
            js_code = js_code.replace("{{new_styles}}", str(new_styles))
            js_code = js_code.replace("{{rndchars}}", f".highlight_{self.rndchars}")
            # Inject js_code into the DOM
            try:
                script_index = self._code_blocks[2].object.index("<script>")
                prev_mlir = self._code_blocks[2].object[:script_index]
            except ValueError:
                prev_mlir = self._code_blocks[2].object
            self._code_blocks[2].object = prev_mlir + js_code

    def code_callback(self, target, event, offset=0):
        # Find which code block the target is, then update the saved stages
        cb_idx = self._code_blocks.index(target)

        try:
            ipass = self._find_pass_index(event.new)
            self._shown_stages[cb_idx] = ipass + offset
        except KeyError:
            target.object = f"No pass found named {event.new}"
            return

        self.rebuild(cb_idx)

    def button_callback(self, target, event):
        # self._find_pass_index returns 1-based indices ; need 0-based
        ipass = self._find_pass_index(target.value) - 1
        if event.obj.name == "\u25c0":
            target.value = self.passes[max(ipass - 1, 0)]
        elif event.obj.name == "\u25b6":
            target.value = self.passes[min(ipass + 1, len(self.passes) - 1)]

    def enable_button(self, target, event):
        target.disabled = False

    def apply_changes(self, target, event):
        event.obj.disabled = True

        # Generate a new debug result
        input_mlir = self._ui["edit_text"].value.encode()
        passes = [p.strip() for p in self._ui["ckbox_passes"].value]
        new_results = self.dr._cli.debug_passes(input_mlir, passes)
        self.dr = new_results
        self.passes = [
            f"[{i+1}/{len(self.dr.passes)}] {p}" for i, p in enumerate(self.dr.passes)
        ]

        # Reset back to original code views
        self._shown_stages = [0, 1, 0, 0, 1]

        # Update all Select dropdowns with available passes
        for sel_str in (
            "seq_select",
            "sgl_select",
            "dbl_select_left",
            "dbl_select_right",
        ):
            sel = self._ui[sel_str]
            if sel_str != "seq_select":
                sel.options = [self.initial_pass] + self.passes
                sel.value = (
                    self.initial_pass
                    if sel_str != "dbl_select_right"
                    else self.passes[0]
                )
            else:
                sel.options = self.passes
                sel.value = self.passes[0]

        self.rebuild()
