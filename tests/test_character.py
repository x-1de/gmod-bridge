import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gm2b.playermodel_builder import standard_skeleton, validate_markers


class CharacterTests(unittest.TestCase):
    def markers(self):
        out = {"groin": [0, 0.8, 0], "chin": [0, 1.6, 0]}
        for s, sign in (("r", -1), ("l", 1)):
            for k, x, z in (("shoulder", .2, 1.4), ("elbow", .4, 1.3), ("wrist", .6, 1.2),
                            ("knee", .1, .5), ("ankle", .1, .1)):
                out[f"{k}_{s}"] = [sign * x, z, 0]
        return out

    def test_reject_invalid_or_collapsed_joints(self):
        validate_markers(self.markers(), 1.8)
        for value in (float("nan"), float("inf"), "invalid", None):
            markers = self.markers()
            markers["chin"][0] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_markers(markers, 1.8)
        markers = self.markers()
        markers["elbow_r"] = markers["wrist_r"][:]
        with self.assertRaisesRegex(ValueError, "слишком близко"):
            validate_markers(markers, 1.8)

    def test_optional_depth_and_missing_markers(self):
        markers = {k: v[:2] + [None] for k, v in self.markers().items()}
        validate_markers(markers, 1.8)
        del markers["wrist_l"]
        with self.assertRaisesRegex(ValueError, "wrist_l"):
            validate_markers(markers, 1.8)

    def test_extra_palms_preserve_animation_hierarchy(self):
        # Original synthetic reference: no Valve model data is stored in tests.
        bones = []
        for s in ("R", "L"):
            hand = len(bones)
            bones.append(SimpleNamespace(name=f"ValveBiped.Bip01_{s}_Hand", parent=-1,
                                         pos=(0, 0, 0), quat=(0, 0, 0, 1)))
            for digit in range(5):
                for j, suffix in enumerate(("", "1", "2")):
                    bones.append(SimpleNamespace(name=f"ValveBiped.Bip01_{s}_Finger{digit}{suffix}",
                                                 parent=hand if j == 0 else len(bones) - 1,
                                                 pos=(1, digit * .2 if j == 0 else 0, 0), quat=(0, 0, 0, 1)))
        # Pad to the real reference count so accidental slicing regressions are caught.
        while len(bones) < 56:
            bones.append(SimpleNamespace(name=f"helper{len(bones)}", parent=0,
                                         pos=(0, 0, 0), quat=(0, 0, 0, 1)))
        with patch("gm2b.playermodel_builder.game_sources", return_value=[]), \
                patch("gm2b.playermodel_builder.VirtualFS") as vfs, \
                patch("gm2b.playermodel_builder.Mdl", return_value=SimpleNamespace(bones=bones)):
            vfs.return_value.read.return_value = b"fixture"
            basic = standard_skeleton("game", "male", 56)
            expanded = standard_skeleton("game", "male", 64)
        self.assertEqual(len(expanded), 64)
        self.assertEqual(expanded[:56], basic)
        for b in expanded[56:]:
            self.assertTrue(expanded[b["parent"]]["name"].endswith("_Hand"))
            root = next(r for r in expanded if r["name"] == b["palm_tip"])
            self.assertEqual(root["parent"], b["parent"])
            self.assertEqual(b["pos"], [p * .3 for p in root["pos"]])


if __name__ == "__main__":
    unittest.main()
