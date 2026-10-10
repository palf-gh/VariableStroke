"""Catch PyObjC selector/argument mismatches before Glyphs loads the class."""
import ast
import math
import types
import uuid
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / 'VariableStrokeTool.glyphsTool/Contents/Resources/plugin.py'


class SelectorTests(unittest.TestCase):
    def test_virtual_add_skips_incompatible_masters(self):
        module = ast.parse(PLUGIN.read_text())
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef)
                      and node.name == 'VariableStrokeTool')
        methods = [node for node in plugin.body if isinstance(node, ast.FunctionDef)
                   and node.name in ('_virtual_siblings', '_add_virtual_at')]
        methods = [ast.FunctionDef(name=node.name, args=node.args, body=node.body,
                                   decorator_list=[], returns=None) for node in methods]
        namespace = {'uuid': uuid, 'VIRTUAL_KEY': 'virtual',
                     'segments_for_path': lambda path: path.segments,
                     'virtual_nodes': lambda path: path.attributes.get('virtual', []),
                     '_invalidate': lambda layer, paths: None}
        exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                     str(PLUGIN), 'exec'), namespace)

        class Layer:
            def __init__(self, segments):
                self.paths = [types.SimpleNamespace(segments=segments, attributes={},
                                                   closed=False)]
                self.paths[0].parent = self
                self.changes = 0

            def beginChanges(self): self.changes += 1
            def endChanges(self): self.changes -= 1

        segment = ('line', ((0, 0), (0, 100)), None, None)
        current, different, compatible = (Layer([segment]), Layer([segment, segment]),
                                          Layer([segment]))
        glyph = types.SimpleNamespace(layers=[current, different, compatible])
        for layer in glyph.layers:
            layer.parent = glyph

        class Owner:
            _virtual_siblings = namespace['_virtual_siblings']
            _virtual_adding = True
            _virtual_error = ''

            def _nearest_virtual_position(self, path, point, defaults):
                return (0, 0, 0.5)
            def _select_tab(self, tab): pass
            def _refresh_ui(self): pass
            def _redraw(self): pass

        owner = Owner()
        self.assertTrue(namespace['_add_virtual_at'](
            owner, current, (0, 50), [(current.paths[0], None)], None, 8))
        self.assertEqual(len(current.paths[0].attributes['virtual']), 1)
        self.assertNotIn('virtual', different.paths[0].attributes)
        self.assertEqual(len(compatible.paths[0].attributes['virtual']), 1)
        self.assertEqual([layer.changes for layer in glyph.layers], [0, 0, 0])

    def test_refresh_dispatches_to_each_inspector_tab(self):
        module = ast.parse(PLUGIN.read_text())
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef)
                      and node.name == 'VariableStrokeTool')
        method = next(node for node in plugin.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_refresh_ui')
        method = ast.FunctionDef(name=method.name, args=method.args, body=method.body,
                                 decorator_list=[], returns=None)
        namespace = {'profiles': lambda font: {}, 'sorted_profiles': lambda library: [],
                     'profile_id': lambda path: None}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     str(PLUGIN), 'exec'), namespace)

        class Inspector:
            _updating_ui = False
            _live = None
            _last_ui_state = None
            _virtual_adding = False
            _virtual_error = ''
            _sampling = False
            _sample_error = ''
            _fields = {}
            infoBoxWindow = types.SimpleNamespace(group=types.SimpleNamespace(
                virtualTab='virtual', nodeTab='node', capsTab='caps', cornerTab='corner',
                profileTab='profile'))

            def __init__(self, tab):
                self._tab = tab
                self.called = []

            def _is_current(self): return True
            def _layer(self): return None
            def _virtual_current(self, layer): return None, None
            def _show_stroke_row(self, *args): pass
            def _show_virtual_tab(self, *args): self.called.append(('virtual', len(args)))
            def _show_node_tab(self, *args): self.called.append(('node', len(args)))
            def _show_caps_tab(self, *args): self.called.append(('caps', len(args)))
            def _show_corner_tab(self, *args): self.called.append(('corner', len(args)))
            def _show_profile_tab(self, *args): self.called.append(('profile', len(args)))

        for tab, argc in (('virtual', 3), ('node', 3), ('caps', 3), ('corner', 4),
                          ('profile', 4)):
            with self.subTest(tab=tab):
                inspector = Inspector(tab)
                namespace['_refresh_ui'](inspector)
                self.assertEqual(inspector.called, [(tab, argc)])

    def test_position_field_accepts_overlap_values(self):
        module = ast.parse(PLUGIN.read_text())
        parser = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_parse_field')
        namespace = {'math': math}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[parser], type_ignores=[])),
                     str(PLUGIN), 'exec'), namespace)
        self.assertEqual(namespace['_parse_field']('offset', '175'), 175)
        self.assertEqual(namespace['_parse_field']('offset', '-225'), -225)
        self.assertIsNone(namespace['_parse_field']('offset', 'inf'))

    def test_option_width_drag_can_cross_centerline(self):
        module = ast.parse(PLUGIN.read_text())
        helper = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_one_sided_drag_values')
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[])),
                     str(PLUGIN), 'exec'), namespace)
        values = namespace['_one_sided_drag_values'](-5, 10, 10, 100, 0, 1)
        self.assertEqual(values, (25.0, -300.0))
        # The opposite edge stays at its original distance from the centerline.
        scale, offset = values
        self.assertAlmostEqual((1 - offset/100) * scale, 100)

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
        self.assertIn('for item in menu.itemArray()', source)
        self.assertIn('parent.setRepresentedObject_(self._menu_marker)', source)
        self.assertIn('class VariableStrokeMenuMarker', source)
        self.assertNotIn('item.representedObject() ==', source)

    def test_second_context_callback_does_not_add_another_menu(self):
        module = ast.parse(PLUGIN.read_text())
        context = next(node for node in module.body if isinstance(node, ast.ClassDef)
                       and node.name == 'VariableStrokeContextMenu')
        helper = next(node for node in context.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_already_has_menu')
        helper = ast.FunctionDef(name=helper.name, args=helper.args, body=helper.body,
                                 decorator_list=[], returns=None)
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[])),
                     str(PLUGIN), 'exec'), namespace)

        class Marker:
            pass

        class Item:
            def representedObject(self):
                return Marker()

        class Menu:
            def itemArray(self):
                return [Item()]

        class Owner:
            _menu_marker = Marker()

        self.assertTrue(namespace['_already_has_menu'](Owner(), Menu()))

        class OtherItem:
            def representedObject(self):
                return object()

        class OtherMenu:
            def itemArray(self):
                return [OtherItem()]

        self.assertFalse(namespace['_already_has_menu'](Owner(), OtherMenu()))

    def test_settings_menu_is_reused_when_tool_starts_again(self):
        source = PLUGIN.read_text()
        self.assertIn('for item in menu.submenu().itemArray()', source)
        self.assertIn('existing_items[0].setTarget_(self._inspector_provider)', source)
        self.assertIn('duplicate.menu().removeItem_(duplicate)', source)

    def test_closed_settings_window_is_recreated_before_reload(self):
        module = ast.parse(PLUGIN.read_text())
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef)
                      and node.name == 'VariableStrokeTool')
        method = next(node for node in plugin.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_show_settings')
        method = ast.FunctionDef(name=method.name, args=method.args, body=method.body,
                                 decorator_list=[], returns=None)

        class Settings:
            def __init__(self):
                self.visible = False
                self.opens = 0

            def is_open(self): return self.visible

            def open(self):
                self.opens += 1
                self.visible = True

        namespace = {'VariableStrokeSettings': Settings}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     str(PLUGIN), 'exec'), namespace)
        owner = types.SimpleNamespace(_settings=None)
        namespace['_show_settings'](owner)
        first = owner._settings
        namespace['_show_settings'](owner)
        self.assertIs(owner._settings, first)
        first.visible = False
        namespace['_show_settings'](owner)
        self.assertIsNot(owner._settings, first)
        self.assertEqual((first.opens, owner._settings.opens), (2, 1))

    def test_curve_cap_mode_skips_round_and_ellipse_paths(self):
        module = ast.parse(PLUGIN.read_text())
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef)
                      and node.name == 'VariableStrokeTool')
        method = next(node for node in plugin.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_set_curve_cap')
        method = ast.FunctionDef(name=method.name, args=method.args, body=method.body,
                                 decorator_list=[], returns=None)
        namespace = {'CAP_START_KEY': 'start', 'CAP_END_KEY': 'end',
                     'CAP_START_CURVE_ON_KEY': 'start_curve',
                     'CAP_END_CURVE_ON_KEY': 'end_curve',
                     '_invalidate': lambda layer, paths: None}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     str(PLUGIN), 'exec'), namespace)
        paths = [types.SimpleNamespace(attributes={'end': style}, closed=False)
                 for style in ('flat', 'horizontal', 'round', 'ellipse')]

        class Owner:
            _last_ui_state = None
            def _layer(self): return layer
            def _edit_paths(self, layer): return paths
            def _refresh_ui(self): pass
            def _redraw(self): pass

        layer = types.SimpleNamespace(beginChanges=lambda: None, endChanges=lambda: None)
        namespace['_set_curve_cap'](Owner(), 'end', True)
        self.assertEqual([path.attributes.get('end_curve') for path in paths],
                         [True, True, None, None])

    def test_caps_tab_disables_curve_mode_for_round_and_ellipse(self):
        module = ast.parse(PLUGIN.read_text())
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef)
                      and node.name == 'VariableStrokeTool')
        method = next(node for node in plugin.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_show_caps_tab')
        method = ast.FunctionDef(name=method.name, args=method.args, body=method.body,
                                 decorator_list=[], returns=None)

        class Control:
            def __init__(self):
                self.enabled = None
                self.value = None
            def enable(self, value): self.enabled = value
            def set(self, value): self.value = value
            def show(self, value): self.value = value
            def getNSSegmentedButton(self): return self
            def getNSTextField(self): return self
            def setSelectedSegment_(self, value): self.value = value
            def setTextColor_(self, value): pass

        tab = types.SimpleNamespace(**{name: Control() for name in (
            'curveHint', 'startCap', 'endCap', 'startAngle', 'endAngle',
            'startCurveMode', 'endCurveMode')})
        namespace = {'CAP_VALUES': ['flat', 'round', 'ellipse', 'square',
                                    'horizontal', 'vertical', 'angle'],
                     'NSColor': types.SimpleNamespace(labelColor=lambda: None,
                                                      secondaryLabelColor=lambda: None),
                     '_same': lambda values: len(set(values)) == 1}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     str(PLUGIN), 'exec'), namespace)
        namespace['_show_caps_tab'](None, tab, True,
                                    (('round', 'ellipse', 45, 45, False, False),))
        self.assertFalse(tab.startCurveMode.enabled)
        self.assertFalse(tab.endCurveMode.enabled)
        namespace['_show_caps_tab'](None, tab, True,
                                    (('flat', 'horizontal', 45, 45, True, False),))
        self.assertTrue(tab.startCurveMode.enabled)
        self.assertTrue(tab.endCurveMode.enabled)
        self.assertEqual((tab.startCurveMode.value, tab.endCurveMode.value), (1, 0))

    def test_eyedropper_applies_clicked_source_to_selected_destination(self):
        module = ast.parse(PLUGIN.read_text())
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef)
                      and node.name == 'VariableStrokeTool')
        method = next(node for node in plugin.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_sample_at')
        method = ast.FunctionDef(name=method.name, args=method.args, body=method.body,
                                 decorator_list=[], returns=None)
        copied = []
        namespace = {'copy_stroke_settings': lambda source, target:
                     copied.append((source, target)) or True,
                     '_invalidate': lambda layer, paths: None,
                     '_loc': lambda english, japanese: english}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     str(PLUGIN), 'exec'), namespace)
        target, source = object(), object()
        changes = []
        layer = types.SimpleNamespace(paths=[target, source],
                                      beginChanges=lambda: changes.append('begin'),
                                      endChanges=lambda: changes.append('end'))

        class Owner:
            _sampling = True
            _sample_targets = (target,)
            _sample_error = ''
            _tab = 'node'
            _last_ui_state = None
            def _nearest_virtual_position(self, path, point, defaults):
                return (0, 0, 0.5)
            def _show_tab(self, tab): pass
            def _refresh_ui(self): pass
            def _redraw(self): pass

        owner = Owner()
        self.assertTrue(namespace['_sample_at'](
            owner, layer, (0, 0), [(target, None), (source, None)], None, 10))
        self.assertEqual(copied, [(source, target)])
        self.assertEqual(changes, ['begin', 'end'])
        self.assertFalse(owner._sampling)

    def test_inspector_uses_glyphs_callback(self):
        source = PLUGIN.read_text()
        module = ast.parse(source)
        provider = next(node for node in module.body if isinstance(node, ast.ClassDef)
                        and node.name == 'VariableStrokeInspectorProvider')
        self.assertIn('inspectorViewControllersForLayer_',
                      {item.name for item in provider.body if isinstance(item, ast.FunctionDef)})
        self.assertIn("'GSInspectorViewControllersCallback'", source)
        # Kept as a fallback (INSPECTOR_MODE_KEY = 'callback'); the SDK hook is the default.
        self.assertIn('addCallback_forOperation_(self._inspector_provider,', source)
        self.assertIn('self.inspectorDialogView = None if _inspector_via_callback() else view',
                      source)

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
        self.assertIn('PANEL_SIZE = (600, 105)', source)
        for control in ('radiusLink', 'tensionLink', 'ratioLink'):
            self.assertIn('corner.' + control + ' = ImageButton(', source)
        self.assertIn('setTranslatesAutoresizingMaskIntoConstraints_(False)', source)
        self.assertIn("{'title': 'ON'}, {'title': 'OFF'}", source)
        self.assertIn('cap.startCap = SegmentedButton(', source)
        self.assertIn('cap.endCap = SegmentedButton(', source)
        self.assertIn("{'imageObject': _cap_icon(value)", source)

    def test_inspector_builds_every_cap_control_it_uses(self):
        module = ast.parse(PLUGIN.read_text())
        plugin = next(node for node in module.body if isinstance(node, ast.ClassDef)
                      and node.name == 'VariableStrokeTool')
        method = next(node for node in plugin.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_build_inspector')
        attributes = [node for node in ast.walk(method)
                      if isinstance(node, ast.Attribute) and
                      isinstance(node.value, ast.Name) and node.value.id == 'cap']
        assigned = {node.attr for node in attributes if isinstance(node.ctx, ast.Store)}
        used = {node.attr for node in attributes if isinstance(node.ctx, ast.Load)}
        self.assertFalse(used - assigned, used - assigned)

    def test_panel_shows_one_tab_below_the_stroke_row(self):
        source = PLUGIN.read_text()
        self.assertIn('group.tabs = SegmentedButton(', source)
        for tab in ('nodeTab', 'cornerTab'):
            self.assertIn('group.' + tab + ' = Group((0, TAB_TOP, -0, 24))', source)
        self.assertIn('group.capsTab = Group((0, TAB_TOP, -0, 48))', source)
        self.assertIn('group.virtualTab = Group((0, TAB_TOP, -0, 72))', source)
        self.assertIn("('virtual', 'Virtual', '仮想')", source)
        self.assertIn('group.profileTab = Group((0, TAB_TOP, -0, 48))', source)
        self.assertIn("('profile', 'Profile', 'プロファイル')", source)


if __name__ == '__main__':
    unittest.main()
