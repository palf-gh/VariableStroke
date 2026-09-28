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

    def test_context_callback_selector_signature(self):
        module = ast.parse(PLUGIN.read_text())
        context = next(node for node in module.body if isinstance(node, ast.ClassDef)
                       and node.name == 'VariableStrokeContextMenu')
        callback = next(node for node in context.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'contextMenuCallback_forSelectedLayers_event_')
        self.assertEqual(len(callback.args.args) - 1, 3)

    def test_inspector_actions_exist(self):
        module = ast.parse(PLUGIN.read_text())
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef)
                      and node.name == 'VariableStrokeTool')
        methods = {method.name for method in plugin.body if isinstance(method, ast.FunctionDef)}
        for name in ('activate', 'deactivate', 'toggleFromInspector_',
                     'resetWidthFromInspector_', 'showSettingsFromInspector_',
                     'startCapFromInspector_', 'endCapFromInspector_', '_field_edit',
                     '_apply_field'):
            self.assertIn(name, methods)

    def test_context_menu_is_global_submenu(self):
        source = PLUGIN.read_text()
        self.assertIn('contextMenuCallback_forSelectedLayers_event_', source)
        self.assertIn('parent.setSubmenu_(submenu)', source)
        self.assertIn('getattr(self, action)', source)
        self.assertIn('submenu.setAutoenablesItems_(False)', source)
        self.assertIn('GSCallbackHandler.addCallback_forOperation_', source)

    def test_inspector_uses_glyphs_callback(self):
        source = PLUGIN.read_text()
        module = ast.parse(source)
        provider = next(node for node in module.body if isinstance(node, ast.ClassDef)
                        and node.name == 'VariableStrokeInspectorProvider')
        self.assertIn('inspectorViewControllersForLayer_',
                      {item.name for item in provider.body if isinstance(item, ast.FunctionDef)})
        self.assertIn("'GSInspectorViewControllersCallback'", source)
        self.assertIn('addCallback_forOperation_(self._inspector_provider, INSPECTOR_CALLBACK)', source)

    def test_outline_is_built_in_glyphs_prepared_layer(self):
        source = PLUGIN.read_text()
        module = ast.parse(source)
        processor = next(node for node in module.body if isinstance(node, ast.ClassDef)
                         and node.name == 'VariableStrokeLayerProcessor')
        method = next(item for item in processor.body if isinstance(item, ast.FunctionDef)
                      and item.name == 'processLayer_extraHandles_error_')
        self.assertEqual(len(method.args.args) - 1, 3)
        self.assertIn("objc.signature(b'Z@:@@o^@')", source)
        self.assertIn("'GSPrepareLayerCallback'", source)
        self.assertIn('addCallback_forOperation_(self._layer_processor, PREPARE_LAYER_CALLBACK)', source)

    def test_fields_preview_live_and_tab_between_each_other(self):
        source = PLUGIN.read_text()
        module = ast.parse(source)
        delegate = next(node for node in module.body if isinstance(node, ast.ClassDef)
                        and node.name == 'VariableStrokeFieldDelegate')
        methods = {item.name: len(item.args.args) - 1 for item in delegate.body
                   if isinstance(item, ast.FunctionDef)}
        self.assertEqual(methods['controlTextDidChange_'], 1)
        self.assertEqual(methods['controlTextDidEndEditing_'], 1)
        self.assertEqual(methods['stepped_'], 1)
        self.assertIn('setNextKeyView_', source)
        self.assertIn('disableUndoRegistration', source)
        # Passing through a field (focus only) must not apply anything.
        self.assertIn("if commit and str(text) == self._shown.get(name):", source)

    def test_panel_has_distinct_on_off_controls(self):
        source = PLUGIN.read_text()
        self.assertIn('InspectorGroup(', source)
        self.assertIn('PANEL_SIZE = (530, 78)', source)
        self.assertIn('setTranslatesAutoresizingMaskIntoConstraints_(False)', source)
        self.assertIn("{'title': 'ON'}, {'title': 'OFF'}", source)
        self.assertIn('group.startCap = SegmentedButton(', source)
        self.assertIn('group.endCap = SegmentedButton(', source)
        self.assertIn("{'imageObject': _cap_icon(value)", source)


if __name__ == '__main__':
    unittest.main()
