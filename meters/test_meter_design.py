"""Portable checks for gauge direction, demo states, and palette contrast."""
import ast
import math
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import native_data

ROOT = Path(__file__).resolve().parent
NATIVE = (ROOT/'native_app.py').read_text()
BROWSER = (ROOT/'index.html').read_text()


def luminance(hex_color):
    rgb = [int(hex_color[i:i+2], 16)/255 for i in (1,3,5)]
    linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in rgb]
    return sum(v*w for v,w in zip(linear, (.2126,.7152,.0722)))


def contrast(foreground, background):
    low, high = sorted((luminance(foreground), luminance(background)))
    return (high+.05)/(low+.05)


class MeterDesignTests(unittest.TestCase):
    def test_zero_points_up_and_endpoints_stay_left_to_right(self):
        function = next(n for n in ast.parse(NATIVE).body
                        if isinstance(n, ast.FunctionDef) and n.name == 'position')
        namespace = {'math':math}
        exec(compile(ast.Module(body=[function],type_ignores=[]), 'position', 'exec'), namespace)
        position = namespace['position']
        left, top, right = [position(h) for h in (-8,0,8)]
        self.assertLess(left[0],0)
        self.assertAlmostEqual(top[0],0)
        self.assertLess(top[1],0)
        self.assertGreater(right[0],0)
        self.assertEqual(position(-100),left)
        self.assertEqual(position(100),right)

    def resize_view(self, width, height):
        # Execute the actual resize callback with only AppKit boundary calls
        # stubbed, so these geometry checks also run on non-macOS machines.
        tree = ast.parse(NATIVE)
        view_class = next(n for n in tree.body
                          if isinstance(n, ast.ClassDef) and n.name == 'MeterView')
        method = next(n for n in view_class.body
                      if isinstance(n, ast.FunctionDef) and n.name == 'setFrameSize_')
        constants = [n for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(name, ast.Name) and name.id in
                             ('WIDTH', 'STACK_WIDTH', 'WRAP_WIDTH', 'WRAP_ASPECT')
                             for target in n.targets for name in ast.walk(target))]
        namespace = {
            'objc': SimpleNamespace(super=lambda *args: SimpleNamespace(
                setFrameSize_=lambda size: None)),
            'MeterView': object, 'NSMakeRect': lambda *args: args,
        }
        exec(compile(ast.Module(body=constants+[method], type_ignores=[]),
                     'native resize callback', 'exec'), namespace)
        view = SimpleNamespace(setNeedsDisplay_=lambda value: None)
        view.setBounds_ = lambda bounds: setattr(view, 'bounds', bounds)
        namespace['setFrameSize_'](view, SimpleNamespace(width=width, height=height))
        return view

    def test_corner_resize_scales_without_changing_orientation(self):
        for width, height, horizontal in ((772, 446, True), (396, 790, False)):
            for factor in (.75, 1, 1.5):
                with self.subTest(horizontal=horizontal, factor=factor):
                    view = self.resize_view(width*factor, height*factor)
                    self.assertEqual(view.horizontal, horizontal)
                    # Equal x/y scaling preserves circular dials.
                    self.assertAlmostEqual(width*factor/view.bounds[2], factor)
                    self.assertAlmostEqual(height*factor/view.bounds[3], factor)

    def test_squashing_width_stacks_and_widening_restores_row(self):
        self.assertTrue(self.resize_view(772, 446).horizontal)
        self.assertFalse(self.resize_view(396, 446).horizontal)
        self.assertTrue(self.resize_view(772, 446).horizontal)

    def test_text_and_meaningful_graphics_have_aa_contrast(self):
        # Brightest conservative glass surface bounds neutral labels.
        # Warning text is lower on the case, below all reflection geometry.
        pairs = [('#c3d1c9','#42584e',4.5), ('#e8eee9','#42584e',4.5),
                 ('#dde7df','#42584e',4.5), ('#ff5b4d','#1f2e27',4.5),
                 ('#a9e3b9','#101916',4.5), ('#f3c54f','#101916',4.5),
                 ('#ff5148','#08110f',3), ('#68d58b','#08110f',3),
                 ('#f3c54f','#08110f',3), ('#adc1b5','#42584e',3),
                 ('#93aa9c','#42584e',3), ('#ffffff','#42584e',3)]
        for fg,bg,minimum in pairs:
            with self.subTest(foreground=fg,background=bg):
                self.assertIn(fg, NATIVE+BROWSER)
                self.assertGreaterEqual(contrast(fg,bg),minimum)

    def test_demo_states_do_not_read_accounts(self):
        with patch.object(native_data,'fetch_provider',side_effect=AssertionError('live data')):
            for state in ('normal','unavailable','expired','stale','hold','hard','behind','on_pace'):
                for provider in ('Codex','Claude'):
                    result = native_data.demo_provider(provider,state)
                    self.assertEqual(result['provider'],provider)
            self.assertEqual(native_data.demo_provider('Codex','unavailable')['windows'],{})
            self.assertIn('HOLD',native_data.demo_provider('Claude','hard')['summary'])
            self.assertTrue(native_data.demo_provider('Codex','stale')['problem'])


if __name__=='__main__':
    unittest.main()
