"""Взаимодействие с основным видом на настоящих GTK-виджетах."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_steps_dialog import READY, spin  # noqa: E402

if READY:
    from gi.repository import Adw, Graphene, Gtk


class Handlers:
    def __getattr__(self, _name):
        return lambda *_args: None


@unittest.skipUnless(READY, "нужен графический сеанс и PyGObject")
class ShellInteractionTest(unittest.TestCase):
    def setUp(self):
        from merci.ui.shell import Shell

        self.window = Adw.ApplicationWindow()
        self.shell = Shell(Handlers())
        self.window.set_default_size(1180, 820)
        self.window.set_content(self.shell)
        self.window.present()
        spin(40)

    def tearDown(self):
        self.window.destroy()
        spin(5)

    def open_panel(self):
        self.shell.open_container_panel()
        spin(35)
        self.assertTrue(self.shell._panel_open)

    def test_panel_close_button_and_scrim(self):
        self.open_panel()
        self.assertTrue(self.shell.scrim_rev.get_reveal_child())
        self.shell.panel.close.emit("clicked")
        spin(35)
        self.assertFalse(self.shell._panel_open)
        self.assertFalse(self.shell.scrim_rev.get_reveal_child())

    def test_panel_distinguishes_inside_and_outside_clicks(self):
        self.open_panel()
        button = self.shell.panel.btn_restart
        found, point = button.compute_point(
            self.shell.panel_rev,
            Graphene.Point().init(button.get_width() / 2, button.get_height() / 2),
        )
        self.assertTrue(found)
        self.shell._on_panel_layer_click(None, 1, point.x, point.y)
        self.assertTrue(self.shell._panel_open)
        self.shell._on_panel_layer_click(None, 1, 2, 2)
        self.assertFalse(self.shell._panel_open)

    def test_hidden_overlays_do_not_intercept_clicks(self):
        from merci.ui.api import AppPage

        self.shell.show_page(AppPage(
            slug="demo", name="Demo", icon="", overline="", version="", package="demo"
        ))
        spin(35)

        def assert_click_reaches(widget):
            found, point = widget.compute_point(
                self.shell.root,
                Graphene.Point().init(widget.get_width() / 2, widget.get_height() / 2),
            )
            self.assertTrue(found)
            target = self.shell.root.pick(point.x, point.y, Gtk.PickFlags.DEFAULT)
            self.assertIsNotNone(target)
            self.assertTrue(target is widget or target.is_ancestor(widget))

        assert_click_reaches(self.shell.library.menu)
        assert_click_reaches(self.shell.fab)
        self.open_panel()
        self.shell.panel.close.emit("clicked")
        spin(35)
        assert_click_reaches(self.shell.library.menu)
        assert_click_reaches(self.shell.fab)

    def test_dock_reveals_with_the_page_and_hides_with_welcome(self):
        from merci.ui.api import AppPage

        self.assertFalse(self.shell.dock_rev.get_reveal_child())
        self.shell.show_page(AppPage(
            slug="demo", name="Demo", icon="", overline="", version="", package="demo"
        ))
        spin(35)
        self.assertTrue(self.shell.dock_rev.get_child_revealed())
        self.shell.show_welcome()
        spin(35)
        self.assertFalse(self.shell.dock_rev.get_child_revealed())


if __name__ == "__main__":
    unittest.main()
