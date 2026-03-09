# ui_components.py — 재사용 가능한 UI 컴포넌트

import tkinter as tk


class Tooltip:
    """기본 툴팁 — 위젯에 마우스를 올리면 짧은 설명 표시."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        if self.tip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify=tk.LEFT, background="#ffffe0",
                         relief=tk.SOLID, borderwidth=1, font=("Arial", 9))
        label.pack(ipadx=4, ipady=2)

    def hide(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class HelpTooltip(Tooltip):
    """긴 설명용 툴팁 — 300ms 지연, 줄바꿈 지원."""
    def __init__(self, widget, text, wraplength=350):
        self.wraplength = wraplength
        self._after_id = None
        super().__init__(widget, text)

    def show(self, event=None):
        if self.tip_window:
            return
        self._after_id = self.widget.after(300, self._do_show)

    def _do_show(self):
        if self.tip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify=tk.LEFT, background="#ffffe0",
                         relief=tk.SOLID, borderwidth=1, font=("Arial", 10),
                         wraplength=self.wraplength)
        label.pack(ipadx=6, ipady=4)

    def hide(self, event=None):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        super().hide()
