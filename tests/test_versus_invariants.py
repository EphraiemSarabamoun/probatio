"""The invariants versus must keep (DESIGN.md 9, V8): ordo's rule fingerprint and errand-rule status, and every
registered ordo and v4 plan fingerprint, are exactly what they were at commit 1777201; the frozen v4 and ordo banks
still load under their pins; versus imports nothing into them. The baseline below was computed on a plain snapshot of
1777201 (git archive), with the same planner calls as fingerprints() makes here. No network, no model.

    python3 -m unittest tests.test_versus_invariants
"""
import ast
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import ordo  # noqa: E402
import probatio as P  # noqa: E402

NAMES = {"individual": "Jane Doe", "organization": "Acme Corp", "nation": "Norland"}
COMPARES = ("individual:organization", "organization:nation", "individual:nation", "individual:organization:nation")

BASELINE_1777201 = {
    'errand_rule_status': 'adopted',
    'ordo|individual:nation|full|both': 'b29b332b1906f1937fc27d3ab52287651bfd3bf9858c3a1bbdec099b397e2e10',
    'ordo|individual:nation|full|off': '11429d78d2963dffb3af72df5e52eae52f238fb29a9f06cd272386eeaded294c',
    'ordo|individual:nation|full|on': 'c81595e3144dd35c3497e644d6f7d69448aade5e59b221773473f1292d5c5847',
    'ordo|individual:nation|quick|both': 'c0b000f17ae2456948136a5a7ff83a3abc72446840ec178ac55253a3a2da5dac',
    'ordo|individual:nation|quick|off': 'e2008087ea255daf43ba5c85d9be97b248c6f00fa83831e032bd3e87ed04f6f8',
    'ordo|individual:nation|quick|on': 'dcb16ab6b6ba033cbd4d7bee91d63afa47977baad16a31513f558d6de0cd691c',
    'ordo|individual:nation|smoke|both': '6575ec37db0eee9579d0def4657204fd503e34aebb0c68d10c2e41ee2d375c39',
    'ordo|individual:nation|smoke|off': 'fb9a6a13a9a21f3217944f3df51e66f9e33b63a55cba71008a74d7fc8456363e',
    'ordo|individual:nation|smoke|on': 'ae27d18149fb6cd414ecd0c7db7bb0db610228a45059776610cc347c500e6c68',
    'ordo|individual:nation|standard|both': '0bef3c34e9b4c226762a8dcc082c571013099f22ae194d4dca47b7851b8759d8',
    'ordo|individual:nation|standard|off': '1af2a95f51006ba95a13acb87db69b29ab2e8c766c3fdb59f08c997896bca224',
    'ordo|individual:nation|standard|on': 'b39f53222b49e1a0cbf0ac5bfadff534ca46329e574a595adcdb7335044ab41d',
    'ordo|individual:organization:nation|full|both': 'ff95619417a59e0fea2311262b02913fa7aa87b1623bf29b3716c27ba256b6ba',
    'ordo|individual:organization:nation|full|off': '3b43bd17c068618bf83f3f1205b25937669edded778d08f22e8ea79592e9ac2e',
    'ordo|individual:organization:nation|full|on': '008c6a5e15d9c2d1f100a3db4f3ffa3382e68ec9feb3df2ec7415398bf357fd4',
    'ordo|individual:organization:nation|quick|both': '0f6d0997f9d655b3d29da58da964a38996acccb03e0653dc6d6ad3f0381c68c2',
    'ordo|individual:organization:nation|quick|off': 'e79086bb6fe169b068c9a2ec1678ddfed220127a27eaf6c69fd3b125b52a90a7',
    'ordo|individual:organization:nation|quick|on': '32efaa106bfc5bba62fe23ad418313be68c732896e324368fe69eb1912f99ec5',
    'ordo|individual:organization:nation|smoke|both': 'f70a6a9916af5a05743be64d37403bb32d3b0a799a4e018285fc8b195e8bc558',
    'ordo|individual:organization:nation|smoke|off': '34e57c506fd90d49740c180bff3ec6b7e5b685f4250601d3a364df0c7c4f6a0b',
    'ordo|individual:organization:nation|smoke|on': '2da661c7423a89ade4236eb61ceea4f41b592fec26ec67bf458d000e34af78de',
    'ordo|individual:organization:nation|standard|both': '5705a96b9baa684500c9e3ba7e84425ec8402759d8a13a2fd409d8c2c82a6c9b',
    'ordo|individual:organization:nation|standard|off': 'fe4a18d1beeb2b9c0315b76889e5235539d09d5772266390c6518d7e35c7b84c',
    'ordo|individual:organization:nation|standard|on': 'e4cce94e0423e05b3b7770b6dfda160cc91f01d9c3c44cf43f088615ccde9d45',
    'ordo|individual:organization|full|both': '8534101f271e7902bdb9d25d0403ca0c297aabd187247647f5f6745ed43d8fc6',
    'ordo|individual:organization|full|off': '2fa04c4aa014d95c790106c22f83a31461bf6bd2c6eff8e3f8086dc8dc29bed3',
    'ordo|individual:organization|full|on': 'd1a049bd0ef798cf7f4b364cf72d09ddbc9a09d687fa18f8ef21669a1a72ffd3',
    'ordo|individual:organization|quick|both': '0c710ce4624411fd07cafed7e642d873319ab0e741fdd3d28ef76aa4e657b8c8',
    'ordo|individual:organization|quick|off': '71b98a799eebde72901cce29bd998bd547354f71028a8a437056ce45a0dd6ae5',
    'ordo|individual:organization|quick|on': '80d379ac9afa6ac9eddf807582605d526b53807772a819dc148d81dee78cd28c',
    'ordo|individual:organization|smoke|both': 'a5af63816f23a39d2a8dabccf4b9afa40c72c795c57f32e62755d756adf09645',
    'ordo|individual:organization|smoke|off': 'b0d832a031a4bda31e686fabbede3b3e2533dc73c1d989f0a1be5f0ba3cf5b65',
    'ordo|individual:organization|smoke|on': '5fd16cbe3da3f60be3a5a15701a2448eedf1c1df73f30d08681b6ad9594a1dab',
    'ordo|individual:organization|standard|both': 'ed169583c9b2b3d562ffd2a963715cf682c408ecbfada989b7cd0da99a3f1e63',
    'ordo|individual:organization|standard|off': '11f4d0520b1dc57d62dc3df36f48511ac541ee628b58ed8654074ec2e090de46',
    'ordo|individual:organization|standard|on': 'aa71e1e3b7a80ab30494a848063744d95f40921b2725c676fa10fddf3ddcbbd9',
    'ordo|organization:nation|full|both': 'e5bc7bb4a49cc0533f6003a5862829e7d7331186dc8b21be0db158d51b5fe1b4',
    'ordo|organization:nation|full|off': '8c1f9dd875ed0a497115e86d32b062043937dfaa66505426fe8a1141cd12057c',
    'ordo|organization:nation|full|on': '88ad922b73251836fa9da2e806ae462a4482281f638a2a4ee5ee49f01bd93988',
    'ordo|organization:nation|quick|both': 'cae205269731b0acccab54a69ab0b3d902c07447b297ea517268f1780cb46f9b',
    'ordo|organization:nation|quick|off': '805ccb1a694be6a735c54d0752ff19b85e72c5e3c3eaf4ecd4285d7f594963df',
    'ordo|organization:nation|quick|on': 'a1fb96bcf610305c1669c4eab98238d12dc07e6e8eb3cb4b86e2d20d886a66fe',
    'ordo|organization:nation|smoke|both': 'c1ff9029c9e2a13dc20826354fb2c93d13306db2a9477bd727e35c06f2b591fe',
    'ordo|organization:nation|smoke|off': '08b99f8192e918c230cd638c759aa4284ebb8fe81c20391279627d5ba876df4f',
    'ordo|organization:nation|smoke|on': '606d13e5707a092fe47fba70dda8731cf329dee92eb288ae6bb14f245188a3fe',
    'ordo|organization:nation|standard|both': '3ded605ceba806bc0eba292a2b785442b61f033ce59b00eaa9fd519a0055ea6e',
    'ordo|organization:nation|standard|off': '65ee091dc95cb1b5f88fb00c11d67ca3e8850d5658509c05a365ebb93947b713',
    'ordo|organization:nation|standard|on': '738217467ce293360e36eca84f4448654e27fd99fd726afbda0054d56f0428f2',
    'rule_fingerprint': 'f8d557b3336db0a5f49252dc846137e57a9027be868ecb6d65303aa2a859f09d',
    'v4|individual|full|both': 'a4ef4809c8628c866927886db51bb17eb520b0b76375b7e6c52b8976de834b37',
    'v4|individual|full|off': 'c0aeeda936df4135a51c3951225fc070bea6e8b06bfec4d4dfd63bf7fbc8d04c',
    'v4|individual|full|on': 'e2d28be6543f0627025010989f0e4122c86da9599a4554a0a307dd6b5f9767ee',
    'v4|individual|quick|both': 'f7da5bbbf1e22f2387492cbc4bf2e5c18d89255e133af286f3de5f49a0794c73',
    'v4|individual|quick|off': '94a0ac4e17d0a61f2e807f21ef48c3bf9c00c2038e0ef0c7aa64a3884153ee98',
    'v4|individual|quick|on': '7ab9aa55258aed0601506d04f3e17cb0e491b589b1903a64cc2572935ef13a34',
    'v4|individual|smoke|both': 'afb66f84859193cb8a8af28cce51fcbc330fe6754bfe52c8ead4b9357b13fe4c',
    'v4|individual|smoke|off': 'a9cf16e19ae045c76e4c02835fafc045118156438574a1ad64e7f4a4fb11ec28',
    'v4|individual|smoke|on': 'a06ea806ad4dfcb5e727dff8a22d9fb9403d65486b99ea6c7b45671b01c7f2f6',
    'v4|individual|standard|both': '406db1a6ed3230751be239f7191375e8d04012ef75a2075d843241a9199e6ab9',
    'v4|individual|standard|off': '839d47aac79777276eddd09900844e4e16396b8c83dcbeb74c299f41274bddd7',
    'v4|individual|standard|on': 'f56e89f8372e5f5596ad28f7089dad4f157856c98541563fb00875b20f9f0ce1',
    'v4|nation|full|both': 'e35b2c6eb11af22a2a131b44406f4909b8f713a7bc2a7e9a6f3f51b3d19ca546',
    'v4|nation|full|off': '1914cb7589b9cd38d0dec7dbfe783c81df04c2fdf4a22aa6a242c1aa49af14f1',
    'v4|nation|full|on': '86d3eb48b0fbf2dcf9eea9f573f1d96a05fec2264b45bf6d1b94a13057cf1532',
    'v4|nation|quick|both': 'bc18626778ed2f7da069fc0296493d57b997db97bdb02df5734a1f6d07b57562',
    'v4|nation|quick|off': '263551d77115700f0beecb371fa069c756321b0ca3133a3a67dbcf352664dcad',
    'v4|nation|quick|on': '7e21c38384227a78201111beb94ac8f0b78549c56148267f8810f1d311154226',
    'v4|nation|smoke|both': '559ee34b88a4cb03edb00ef64ef31cb78f6b966e24a6afd57e96774a55fed09c',
    'v4|nation|smoke|off': 'c5c14af933b26a2be882eafe1d4abe1454587118beb5d0484b9d2c1b563a988b',
    'v4|nation|smoke|on': 'ddc60a7920f9b8e79448bd850fb8e8e2d0d8ba0acde40b5fb2c55173fd81aa33',
    'v4|nation|standard|both': 'd254302dbf34a4cd860124a69079dd49d88be849cb048d50629452d207882acd',
    'v4|nation|standard|off': 'dd22b240cccd574df40c24295809a53a6d7d566657b1857b33c048001e792f4c',
    'v4|nation|standard|on': 'eb1cd6266a83989516f032e2078c2c7912221c24cea8b461d08394d203f24acf',
    'v4|organization|full|both': '2257666717f2acdf993a18a30efc51c5f5b23fb105011a929814fe7e95504b28',
    'v4|organization|full|off': 'd8a4d21cbded4af879fdc390e8da15488f192693658eb02984ccd3cf1881d289',
    'v4|organization|full|on': 'b440e92cfba46a129d23c8f5ae9019df1232854e4cba4a0e47388a33547746be',
    'v4|organization|quick|both': 'f8c73913889e777f6b14ddb1bab4ccbc03d0a1419b23fbe50803cea12e4bc048',
    'v4|organization|quick|off': '0e562d5348a2bfb84c3032536891a89bf5bdd7cb15dc59703a421589bf82d7e7',
    'v4|organization|quick|on': 'cd1874b121ef85c266b37ec36190bdf65c68f72d44eea78b98d5b32e13b062df',
    'v4|organization|smoke|both': 'faec14f51d8f8ad8568028f48df567b4e712ea604e7440c58d140a0994307a8c',
    'v4|organization|smoke|off': '2eb33c37c64c117bd427ba375b25e8a5085b5c472cced122f81f325b706c1aa9',
    'v4|organization|smoke|on': 'edad590b3b8fe674873e0ea59aba99385b9de0061655908e67d4bb775972bff8',
    'v4|organization|standard|both': '0a4e4b8c5f051e08005ef69ba05deb4d50f3b21f154788815bf564e74581b48c',
    'v4|organization|standard|off': '9b26de1225a5c3049ae92dc2354ae3aa1f2852aaace2fb5afa9f4c527042a25e',
    'v4|organization|standard|on': '8ed3a4292347392bee0892ad363d2683a23ecae954d38c553d05af9f54370f1b',
}


def fingerprints():
    """Today's values, computed as the baseline was: ordo's rule fingerprint and errand-rule status; v4's plan for
    each type (a typed name), tier and memory mode with the tier's classes; ordo's plan for each comparison, tier and
    memory mode."""
    b, ob = P.load_bank(REPO / "bank"), ordo.load_ordo_bank()
    out = {"rule_fingerprint": ordo.rule_fingerprint(), "errand_rule_status": ordo.errand_rule_status()["status"]}
    for t in P.TARGET_TYPES:
        roster = P.resolve_target(b, t, NAMES[t])
        for tier in P.V4_TIERS:
            for mode, conds in sorted(P.MEMORY_MODES.items()):
                out[f"v4|{t}|{tier}|{mode}"] = P.plan_fingerprint(
                    P.plan_v4(b, roster, tier, P.resolve_classes(tier, "all"), conds))
    for compare in COMPARES:
        for tier in ordo.ORDO_TIERS:
            for mode, conds in sorted(P.MEMORY_MODES.items()):
                out[f"ordo|{compare}|{tier}|{mode}"] = P.plan_fingerprint(ordo.plan(ob, compare, tier, conds))
    return out


class Invariants(unittest.TestCase):
    def test_every_fingerprint_and_the_rule_status_as_at_1777201(self):
        now = fingerprints()
        self.assertEqual(sorted(now), sorted(BASELINE_1777201))
        for k in sorted(now):
            self.assertEqual(now[k], BASELINE_1777201[k], k)
        self.assertEqual(now["errand_rule_status"], "adopted")

    def test_the_frozen_banks_load_under_their_pins(self):
        self.assertEqual(P.load_bank(REPO / "bank").sha256, P.BANK_MANIFEST_SHA256)
        self.assertEqual(ordo.load_ordo_bank().sha256, ordo.ORDO_MANIFEST_SHA256)

    def test_probatio_and_ordo_never_import_versus(self):
        """ordo and world import nothing of versus, and live and ui hold no import of any versus module (live's versus
        page is handed what it needs by versus_run; ui reaches versus_run only through probatio's one hand-over,
        import_versus_run, called from its one accessor, versus_command); probatio imports only versus_run, only
        inside that hand-over function (import_versus_run, called for --versus and for --rescore of a versus
        folder)."""
        for name in ("ordo.py", "world.py"):
            text = (REPO / name).read_text(encoding="utf-8")
            self.assertNotIn("import versus", text, name)
            self.assertNotIn("from versus", text, name)
        for name in ("live.py", "ui.py"):
            tree = ast.parse((REPO / name).read_text(encoding="utf-8"))
            mods = [m for n in ast.walk(tree) for m in ([a.name for a in n.names] if isinstance(n, ast.Import) else
                                                        [n.module or ""] if isinstance(n, ast.ImportFrom) else [])]
            self.assertEqual([m for m in mods if m.startswith("versus")], [], name)
            calls = [fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                     for n in ast.walk(fn) if isinstance(n, ast.Attribute) and n.attr == "import_versus_run"]
            self.assertEqual(calls, ["versus_command"] if name == "ui.py" else [], name)
        tree = ast.parse((REPO / "probatio.py").read_text(encoding="utf-8"))
        found = []
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)] + [tree]:
            body = list(ast.walk(fn)) if fn is not tree else [n for n in tree.body]
            for node in body:
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                        [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                found += [(getattr(fn, "name", "<module>"), m) for m in mods if m.startswith("versus")]
        self.assertEqual(found, [("import_versus_run", "versus_run")])


if __name__ == "__main__":
    unittest.main()
