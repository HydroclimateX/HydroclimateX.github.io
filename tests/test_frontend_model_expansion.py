"""Executable frontend contract tests for variable and model selection."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class FrontendModelExpansionTests(unittest.TestCase):
    def test_variable_selection_helper_runs_in_node(self) -> None:
        script = textwrap.dedent(
            '''
            const assert = require('assert');
            const selection = require('./wasp-app/data-selection.js');

            const columns = selection.parseCsvHeader(
              '\ufeff"flow, observed","SST ""Niño""",date,unused\\r\\n1,2,2020-01-01,x'
            );
            assert.deepStrictEqual(columns, ['flow, observed', 'SST "Niño"', 'date', 'unused']);

            let state = selection.createSelection(columns);
            assert.strictEqual(state.targetColumn, 'flow, observed');
            assert.deepStrictEqual(state.predictorColumns, ['SST "Niño"', 'date', 'unused']);

            state = selection.changeTarget(state, 'SST "Niño"');
            assert.strictEqual(state.targetColumn, 'SST "Niño"');
            assert.deepStrictEqual(state.predictorColumns, ['flow, observed', 'date', 'unused']);
            assert.strictEqual(selection.isSelectionValid(state), true);

            state = selection.clearPredictors(state);
            assert.deepStrictEqual(state.predictorColumns, []);
            assert.strictEqual(selection.isSelectionValid(state), false);
            state = selection.selectAllPredictors(state);
            assert.deepStrictEqual(state.predictorColumns, ['flow, observed', 'date', 'unused']);

            const operations = selection.createOperationGate();
            const stale = operations.begin();
            const current = operations.begin();
            assert.strictEqual(operations.isCurrent(stale), false);
            assert.strictEqual(operations.isCurrent(current), true);
            operations.invalidate();
            assert.strictEqual(operations.isCurrent(current), false);

            assert.throws(() => selection.parseCsvHeader('target,,x\\n1,2,3'), /blank/i);
            assert.throws(() => selection.parseCsvHeader('target,x,x\\n1,2,3'), /unique/i);
            '''
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_request_and_attribution_helpers_run_in_node(self) -> None:
        script = textwrap.dedent(
            """
            const assert = require('assert');
            const helper = require('./wasp-app/data-selection.js');
            const entries = [];
            const form = { append: (key, value) => entries.push([key, value]) };
            helper.appendPredictionFields(form, {
              columns: ['y', 'x1', 'x2'], targetColumn: 'y', predictorColumns: ['x1', 'x2']
            }, 'xgboost');
            assert.deepStrictEqual(entries, [
              ['target_column', 'y'], ['predictor_columns', 'x1'],
              ['predictor_columns', 'x2'], ['model', 'xgboost']
            ]);

            assert.strictEqual(
              helper.attributionPresentation({kind: 'coefficient', items: []}, 'Linear Regression').title,
              'Scaled coefficients — Linear Regression'
            );
            assert.strictEqual(
              helper.attributionPresentation({kind: 'importance', items: []}, 'XGBoost').title,
              'Feature importance — XGBoost'
            );
            assert.match(
              helper.attributionPresentation({kind: 'none', items: []}, 'K-Nearest Neighbors').message,
              /no intrinsic feature attribution/i
            );
            """
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_application_contains_the_new_controls_and_image(self) -> None:
        html = read("wasp-app/index.html")
        self.assertIn('src="data-selection.js"', html)
        self.assertIn('src="assets/WASP.jpg"', html)
        self.assertIn('id="variablePanel"', html)
        self.assertIn('id="targetColumn"', html)
        self.assertIn('id="predictorList"', html)
        self.assertIn('id="model"', html)

        wavelets = re.findall(r'<option value="(db\d+)"', html)
        self.assertEqual(wavelets, ["db1", "db2", "db4", "db8", "db16"])
        self.assertIn('<option value="db4" selected>', html)
        self.assertRegex(
            html,
            r'<option value="db16">[^<]*Python-only[^<]*no upstream R/waveslim equivalent',
        )
        self.assertIn('<link rel="icon" href="data:,">', html)
        self.assertIn('<option value="0.5">50%</option>', html)
        for model in ("linear", "knn", "xgboost"):
            self.assertRegex(html, rf'<option value="{model}"')

        self.assertNotIn('id="alpha"', html)
        self.assertNotIn("Ridge Regression", html)
        self.assertNotIn("sym8", html)
        self.assertNotIn("coif3", html)
        self.assertIn("dataLoadGate.isCurrent(loadToken)", html)
        self.assertIn("predictionGate.isCurrent(runToken)", html)
        self.assertIn("setInterfaceBusy(true)", html)

    def test_nginx_image_bakes_the_tracked_wasp_figure(self) -> None:
        dockerfile = read("nginx/Dockerfile")
        self.assertIn(
            "COPY figs/WASP.jpg /usr/share/nginx/html/assets/WASP.jpg",
            dockerfile,
        )
        self.assertTrue((ROOT / "figs/WASP.jpg").is_file())

    def test_public_documentation_matches_the_selection_contract(self) -> None:
        introduction = read("showcase/wasp-web/index.html")
        example = read("examples/README.md")
        glossary = read("docs/glossary.md")
        combined = "\n".join((introduction, example, glossary))

        for wavelet in ("db1", "db2", "db4", "db8", "db16"):
            self.assertIn(wavelet, introduction)
        for model in ("Linear Regression", "K-Nearest Neighbors", "XGBoost"):
            self.assertIn(model, introduction)
        self.assertIn("select one predictand", introduction.lower())
        self.assertIn('-F "target_column=streamflow_anomaly"', example)
        self.assertGreaterEqual(example.count('-F "predictor_columns='), 2)
        self.assertIn('-F "model=linear"', example)
        self.assertIn("selected predictand", glossary.lower())
        self.assertIn("51 total columns", introduction)
        self.assertIn("51 total columns", example)
        self.assertRegex(
            example,
            r"db16.*Python-only.*no upstream R/waveslim equivalent",
            msg="examples must document db16 as Python-only without an upstream R/waveslim equivalent",
        )
        self.assertNotRegex(combined, r"(?i)ridge regularisation|ridge regression|sym8|coif3")
        self.assertNotIn('-F "alpha=', example)


if __name__ == "__main__":
    unittest.main()
