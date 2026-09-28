"""Catch PyObjC selector/argument mismatches before Glyphs loads the class."""
import ast
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / 'VariableStrokeTool.glyphsTool/Contents/Resources/plugin.py'


class SelectorTests(unittest.TestCase):
    def test_objective_c_selectors_have_matching_arguments(self):
        module = ast.parse(PLUGIN.read_text())
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef)
                      and node.name == 'VariableStrokeTool')
        for method in plugin.body:
            if not isinstance(method, ast.FunctionDef):
                continue
            decorators = {ast.unparse(item) for item in method.decorator_list}
            if 'objc.python_method' in decorators or not method.name.endswith('_'):
                continue
            with self.subTest(method=method.name):
                self.assertEqual(method.name.count('_'), len(method.args.args) - 1)

    def test_inspector_actions_exist(self):
        module = ast.parse(PLUGIN.read_text())
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef)
                      and node.name == 'VariableStrokeTool')
        methods = {method.name for method in plugin.body if isinstance(method, ast.FunctionDef)}
        for name in ('view', 'toggleFromInspector_', 'widthFromInspector_',
                     'startCapFromInspector_', 'endCapFromInspector_',
                     'convertSelectedLayer_'):
            self.assertIn(name, methods)

    def test_context_menu_is_compact(self):
        module = ast.parse(PLUGIN.read_text())
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef)
                      and node.name == 'VariableStrokeTool')
        settings = next(node for node in plugin.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'settings')
        assignment = next(node for node in settings.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Attribute) and
                                  target.attr == 'generalContextMenus' for target in node.targets))
        self.assertEqual(len(assignment.value.elts), 2)


if __name__ == '__main__':
    unittest.main()
