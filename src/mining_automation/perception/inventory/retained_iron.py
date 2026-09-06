"""Positive iron evidence for the retained mining experiment, not a C release.

The frozen V2/V3 candidates remain unchanged. V2 rejects this observed iron
sprite at its perimeter; V3 has no exact full-slot prototype for it. This
additional narrow recognizer requires a full known iron template after
normalizing only source-proven background positions to a sentinel. It never
infers an item from failure to recognize empty, and never learns from a frame.
Small per-slot rendering differences are bounded at every pixel and globally.

Source: diagnostics/third-rock-ore4-20260903/ore-04-reacquired.png,
BGRA SHA256 394049af97149b0d3c61e4306614b6c595b040e205bf9fce0a41ae424aa91778,
slots 0..3. The background palette is from the existing packaged empty profile.
This is replay-validated experimental recognition, not an approved C campaign,
production release, or proof of live 0->28. Unseen sprite variants remain UNKNOWN.
"""

from __future__ import annotations

import hashlib
import zlib
from typing import Final

from ...capture import Frame, PixelFormat
from .classification import SlotOccupancy
from .geometry import InventoryGridLayout, Region
from .positive_classifier_v3 import InventoryPositiveV3DevelopmentResult
from .positive_v3_prototypes import (
    SUPPORTED_COLUMN_STRIDE,
    SUPPORTED_FRAME_HEIGHT,
    SUPPORTED_FRAME_WIDTH,
    SUPPORTED_PROFILE_ID,
    SUPPORTED_REGION,
    SUPPORTED_ROW_STRIDE,
)

BACKGROUND_RGB: Final[frozenset[tuple[int, int, int]]] = frozenset((
    (59, 50, 38),
    (59, 50, 39),
    (60, 51, 39),
    (61, 52, 40),
    (62, 52, 40),
    (62, 53, 40),
    (62, 53, 41),
    (62, 53, 42),
    (63, 53, 42),
    (63, 54, 43),
    (64, 54, 43),
    (64, 54, 44),
    (64, 55, 44),
    (64, 55, 45),
    (64, 56, 44),
    (64, 56, 45),
))

_BACKGROUND_MASK: Final[bytes] = bytes.fromhex(
    "ffffffffffffffffff0fffffff0702ffff0300feff0300fcff0300c0ff030080ff0300000f000000"
    "07000000030000000300000001006000010040000000000000000080000000c0000000e0000000e0"
    "000000e0000000e0000000e0000000c0000008e0810118f0e30138feff01f0ffff01f8ffff01f8ff"
    "ff03fcffff07feff"
)

IRON_TEMPLATE_SHA256: Final[frozenset[str]] = frozenset((
    "76ae2e3cb0b47ee5352b22f32bb93404211a955971874c5b6d40f115c5110349",
    "21277a9c9f4a95043c62da1d521cfe704c38137b52052bab76ee8fa38bb325dd",
    "29e5339c767ceca5e69477c487d7ed34eaa0065fbb7a5e21727d6a164229e973",
    "afec9102fd6fb292b1cb13a80177832cbce6ee043481f6f66fe636da23bade0b",
))


# These are the SAME four source-proven templates identified by the hashes
# above, not templates learned from a live input. The real fifth-ore replay
# differs from the first-row sprite by at most 3 per channel (mean 0.209 on
# foreground channels). Compare every foreground pixel, not mean rock color.
# Background positions still require the exact source-proven palette.
_MAX_IRON_CHANNEL_DELTA: Final[int] = 3
_MAX_IRON_TOTAL_DELTA: Final[int] = 799  # 0.40 * 1998 foreground channels
_IRON_REFERENCE_BYTES: Final[bytes] = zlib.decompress(bytes.fromhex(
    "78daeddaf7539b471a07f0c49e4105550402e133605079d5411554e81d249ae8a2d8c6050c36362e714be2e4"
    "e24b9cc4c9a5dcdf7bdfdd7ddfd5ea9560888c67ee3266def10f46fabefbee3efbec47b23ffbecefff6331d6"
    "6b3575b89261e7c7089f4807d6a7935bf954a3d5c86ec1eff881f745ce60c2bb3b9f3e581ffeeade7c83d910"
    "0cf84d4643b4eb4acad3c62f73bdbeb65b606c73233dfb6b43087ffbb080fcba3a4db8c33114e89c08bbe7e3"
    "deb55468a647d26bea70df6cdc57437e71b68f85b37c6f6be3a0ff1ac2f331ef463ab495ed5e48f8ea359a40"
    "2080e7e2339638dfe3e0950f772690dcf90f3bc22f5dba1cebba321176217cb52f50cc76237fb9d78f7c3cd7"
    "dc70cfed95c1a3cd91e3adb1669bf93cb7c0ace29564d892841122a4cfd336d9ed5e4cf83633e12d9abf9e0e"
    "62dc7dddcedbcb03aff7f318ccbbc72bd717322806d52dcce555c1ae6e6f4728e08f4422e95840abd1f47baf"
    "2d90690f2076835ec56cd8a8d54c6682fbab43df1c2dfcfc74ed8f97c5ef8e97583188e189e0b5a1a4772ce5"
    "9f1d0ccf8ff414c6a3eb33499bc5180fb9b07678c1e79f5f1a0d3a5769382ebbd98091e3baf4f9a5c5b1086a"
    "ec8747cb08ffcfebad5f9f6f60fc78641e1ef1b51567937b85ecddd5c17b1b230f77c68f8aa3871b2366839e"
    "d5a4cb61cd481d0d063dcbc425793c01fa8379c306797e6be6dfcf37108eebb7171bafefe69b1bad7c1db351"
    "e7f585d4bdf561c422fcd9def4a3dd89fb5b6366a31e091a8d36dcd63c1b91b0b883be4e5d5d9ddbed669386"
    "47c3bc6dcfa7df1e2ffdfe82e4fff9aaf8eb17ebceb666ac1adb44a91ed7747f686f29cb46fef8fae4abbbb9"
    "a737a7700bfc96e5a3727251ef52c2870b77c1dcc4436e3ebca3cdd1ef1f2dfffac5c69fafb6fe78b9e96e6f"
    "413806c07e8b399f1b896066c8e07727f0a458a9e7b7679fdc98e2f97dee365296a9c05a2a886ba6c76d35d6"
    "b36d4bf28ba358530c1bf9bfbdd86cb49af02e3c1dde9e8dba51ba4be3b1033af8a77bd35f1dccfdebe1322a"
    "e1e59d9cc524e7a30f201fc9c54c18958f3fe76252032db9b0d461a765fce57e1e93e3ee90079f2153573799"
    "0dad4e253673bd0fb6c6d8ccfcf3c1d2fb67eb582cbcdeaae4a3723033243fdbbd3bd083ca4771e229a6c24e"
    "3c086e8132c08262e42c1c83678f8652dc994fdd5cca1c6f9365fde670e1a7276bbf3c5bc7f5f5bd399e3f16"
    "722e2595fcc1c836cd5feef53599ea519f6403a306e85d7838cb5f9d8cdf591940e59cd0c1ff70b2fc9e8663"
    "7eb00a786ae4632931c8e96e972a1f5bc07fa5a9d77d556ea4063d364b466874c8dfcaa5584d6259df1e17de"
    "3f5dfbf9c9ea4ff4faeec112cb6733a9afabcbc7249edf6c366253c43b5bd14e47825d2b7d416c645523c5bb"
    "6e2c6531ed28956fee2ffef87805b168203f9c90ebfb472bd8bf787454f8ca54e2ceeaa0c36646f3417e8bc5"
    "8869c1a6484bed28d742af1f3b1af96c3062fe9dd5a117b767de1c2ebc3b5979772227775eb52319d7e54b97"
    "670642e857cf6e4ebddecf6139d02d8d3a2d362fee8b4d311e72d18e143c2d1f1df5cb833c0a524ce60d13e1"
    "78c117b766beba37f7e6901c61d8b9f81516110b8a76c73605eb48a858abd120b64de43fda9964e16232de6e"
    "349aa6b2c143251cdb877545b671e88a6826c272bb6b567a1d9e2b1ae9e14bc03a3c9b0a9eccdf5eccf5bdb8"
    "3dfb350d678b8297f11948d2ca57f53ab138f92db0e862327fb49df9342a16d382fc1f1f938a12f3abbe3753"
    "ed14ced2bf57fd8a95d6ab7d391fdb019b0295d664b388335cf5bde73c7c6f1570e4e5be3d5a446f7cff741d"
    "edb1ebaa5d922471866bfe61a58b2315f9d817e88dbf93c66864ebfbe172c3dc36359850f0a8194c7b232eab"
    "9137c60bc16142593efe53591e1ff8c3972f53eb3afefdfd6f926199085dbcffad26c378dacffcdf6435b15b"
    "e050168d57f37d113e989076e753ccffa8529493d9648c7696f9df62d0d7760b9d162aee062c49ff7c586067"
    "4da8ccff38e2259c08c180af861d8dfccdd95e16fe96e6c3ff033e95fffd581ebf3f80e7faab33867ca002e1"
    "683bd8c597a9ffc74bfe0f33ff93e5afd3e48788ff715ec0392d8d96f3dc22197631ff4bdcffeeb6c9b0daff"
    "469da637ecbc05ffdfcd09fe37a96ea1aa0a9c2fd466eddcff781ce6ffd572ff9b74da894cf06eb9ff6931f8"
    "c4f044b0a3d2ffe869ccffd8052affc327dcff0ba39183b532ffa337625178788faf6d13fe5f52fbdf6224fe"
    "371b8d95fef72867a246a3599b4a54fabfa5a981af6326e2bc3e5fc5ffdccfa1b6e69988a7aafff1f6adb994"
    "caffaef61689f9df548f4f67a7fa5ff16794f85fe2feb718e17f171f1e9e54f4bfa7c32129fec76f47fb88ff"
    "efac08fe3f94fdcf7d5be97fe09c5508cbc79afea2f8df4e279ff8df549f89baf3ccffebc395feb752f0f07c"
    "95ff6d26e6ff7656c698734c8ee79a3c781c163a18291364febf5fcdff0d4afe48a04bf4ffb6e0ff06e2ff76"
    "eaff067e86b2630ef9e8308affc7b0ac6f88ff5765ff1fcef17cf2f9a29aff4174c1ffed2aff237f792206ff"
    "63724e76a9ff1f95f9df66a1fe0f13ff4f55f81f2cf409feb754f81ff9c5d9be33fccfc18645a4fef7f07c10"
    "5ded7f9d56d548918f5ea1f87fa1d2ff984fe6ff657ccc81ff1bcd0b71e27f87d58469095d25fe1fe7fed769"
    "5136aaf1a3e911ffdf2bf33f3e1d97fcdf1fc4189e52ff6339ea3575c8c1e6c5b68d96fc1f382d1f7b8afabf"
    "2026f3868970eaff69e67f2c87cb25fb5fabd566a56afe17da26f28fb72798ffc5e472ff4f73ffe3056ce3b0"
    "15190fc9ed0e6bc19b5285ff2d6c2aa472ffd3c30bfe9f11fd8fe5e03390a495afea75e7f73ff2b7e7d4fe17"
    "f33fd0ffc8bfbe98e5f9dcfff646ab38c335fb1ff97b857e95ffb14692e4b910ff231fa5cbfcff4ef17f93d2"
    "182fc4ff40c347f57ff293ffff97beffff18fe4738c4c5fc8f539bddc26a325c88ff113e10e7fe9f23df0afa"
    "7d7072e4e2fc9f1b2af91f9beb34ffe3beb5f97f7da6e47fe44ba7fadf2ffa3f1eea3a673ed08270b41de6ff"
    "a8e2ff15c1fff0399e0b4fcafdef68b29ce716e47468b450ff7b985240854affc3e7bde12ef26508f3ffc9ca"
    "8dc52c1e56750b4b7955b04be57f1c8855fd3f9e0ec0ff6fe0ff27b2ff918f4511c3e3812afec7cb6282ffa1"
    "387edaf2331190c0ebe1ffef15ffc37e102316858797fcbf52e67fe25b5a934ef8dfd36133d40bfe772bfed7"
    "429838a6cbfcbf9f77d86d82ffbb589dabfc8fad846168b5bad0553bf7bf5e53074588fe2fe6d5fea744f7b0"
    "cdc8fc7f13fea78416fd8fc565f92affc32131c5ff7aad066fa4fe5f67fe97aeb5229cfb7fa4d757f2ff4e99"
    "ff793e2a47ed7f93ec7f92bf312cfa9f4d3e9e0e83cf44cef23f050fc93fc5ffa4e4428afff17a4c8eb7531e"
    "7c26eec7e0cff2ff419ee70f9fe17f537d8896717313f13f0b97fdafc5e7ee33fc3fdfa8e48f9ce2ff66c1ff"
    "2161b3f0ca599e889ee17f0a2a3ffcdf40fcef54e5630b10ffbb4afe8f05cbfd4fbfc1e0fec71aa9fcdf6405"
    "48fc98493df57f2e5af2bf83fbdfcffc1fc046c66651e5ef9ee97fd693e1ffc204f17f6ba3659efabf55f6bf"
    "5df4bf89f89c0c46ccbfb53c50e97f7c3ae6fe9fce96fc8fe550fcef661f8a45ff57cdc7cc8bfe67c9bc614e"
    "73a21f10ff63395c2e97e27f5d46ea60dfd570ff37988c71d1ff5acd83ad71e67f31196f3799cc9399000f67"
    "fea73ef40bfeef52fbdfed864e4560a3c32bfef7a8fcbf31d3abf23fcb673350e67fb7fbaffa1f050333bc2c"
    "f7bf5dc867ef0dd5ea7f3d2dad4aff37375ae317e17fe4df5814fdbf06ff936f5d3c17e37f3d2d5d96cffdcf"
    "1be3c5f8dfa6f63f6f8cff2ffeef4f047165e3fe4ffe3ff55b74b3817ed7aa492affa07fb1e163299fec7f9b"
    "391eec52ee58321efbcbdac2fbe3d28ee27f463eeaff56d1ff5683beb65be875dadc6058f6ff7101e3c75913"
    "acf03f1e26e0f7d6d222745acc0c6dcec4ffc8971c55fc8fe3c6e783ff4d7f75c6904fffe344016d87f91f33"
    "53e97f1c5b78aed9c192ff5bedd6f3dc221976b530ff7be4e32ce962fef78afe37ebb5c950d75ea1ff95e07f"
    "5e0ce575a8f63f214dd04f7b4e088f830351edff4cd8a2d78da5d4feb79362f08ae155fd8f97c5824e7463ab"
    "d908ff4371fcb47508fe9f1ba6fe7f58f23f1e1c8bc2c3bbbd5737661295fe27dfdf32ffb750ff1b05ffbb4b"
    "fe5f998c97fb7f13fe6f6db6f1754cf754f73ff77310feef91fd8f0fa45004f73fdebe99ebfbaedcffd23507"
    "568d0dbe375cd5ffe416368b9c8fca29f7bf1114e4c3db5f1b12fd4f08ed91fd8fdf0e9fee7fee67548ecaff"
    "36533dab1096aff8bf88c961934ffc6f36a423aefc10f1ff7e35ff33dff2cf172aff37d2920b79dad9c7d817"
    "b76791efebbac206cfd48a8f968aff4715ff2fbe7fb6c6fccff381d80aff8798fff120b805a0d252e17fe4a3"
    "c39ce17fe667e27f7cbee0fe1f886cf7937c6cde16b3b1e47f8fdaffc8479d9fe6ff6f8f1618d888ff4df593"
    "61a72adf41fe45b2e47fab11fe778a8d4e4fbec1488afe472c2eee7f0e362c22fc3fcbfc4ff34174578be0ff"
    "de8059af533552e4efcca731ed8fe1ffa3857715fe673d99f87f3c8602bbd2649d8f79917fa5c18c690912ff"
    "b781e885a49f3522948da8173dfd92b6d2ffee0e87ecffcb97a7b201ee7f2c87ec7f37f13f36c598e0ffaaf9"
    "285dd1ff2c99374c848bfec77270ffeb74bab4a7231f2df3bf0dfe17da26f2ef6f8eb1703199f97f02fedf90"
    "c3f1a195764533f73f56642c28b73b87f554ffb770ff7bcafccf0eafe7d4ff08678b62a7fee4fe47e59fc7ff"
    "a1aafed7698b39f87f56f47fb390ffa1fed769c5ff5fc4fd8f4d2ace70edfed769c9bf2f94fb1f6bc4fcffe1"
    "1045fe5e41ed7f2cd627ff8b3fa988c4ae4cac9675fc2fb4ebe15b"
))
_IRON_RGB_TEMPLATES: Final[tuple[bytes, ...]] = tuple(
    _IRON_REFERENCE_BYTES[i:i + 3072]
    for i in range(0, len(_IRON_REFERENCE_BYTES), 3072)
)
if len(_IRON_RGB_TEMPLATES) != 4 or any(
    hashlib.sha256(item).hexdigest() not in IRON_TEMPLATE_SHA256
    for item in _IRON_RGB_TEMPLATES
):
    raise ValueError("retained iron reference templates are corrupted")


def _positive_iron_slot(frame: Frame, slot: Region) -> bool:
    normalized = bytearray()
    for y in range(32):
        for x in range(32):
            index = y * 32 + x
            offset = ((slot.y + y) * frame.width + slot.x + x) * 4
            blue, green, red = frame.payload[offset:offset + 3]
            rgb = (red, green, blue)
            if _BACKGROUND_MASK[index // 8] & (1 << (index % 8)):
                if rgb not in BACKGROUND_RGB:
                    return False
                normalized.extend((0, 0, 0))
            else:
                normalized.extend(rgb)
    if hashlib.sha256(normalized).hexdigest() in IRON_TEMPLATE_SHA256:
        return True
    for reference in _IRON_RGB_TEMPLATES:
        total_delta = 0
        for actual, expected in zip(normalized, reference, strict=True):
            delta = abs(actual - expected)
            if delta > _MAX_IRON_CHANNEL_DELTA:
                break
            total_delta += delta
            if total_delta > _MAX_IRON_TOTAL_DELTA:
                break
        else:
            return True
    return False


def retained_iron_count(
    frame: Frame,
    guarded: InventoryPositiveV3DevelopmentResult,
) -> int | None:
    """Recognize a nonempty prefix only with fresh guard and positive slot proof.

    ``guarded`` must be the existing analyzer result for this same ``frame``.
    Geometry and external/gutter guards must already have passed; they yield
    zero slots on failure. Unchanged raw empty decisions retain the 0.8 floor.
    Every occupied slot requires a complete positive template match. Returning
    None supplies no alternative evidence and grants no downstream authority.
    """
    if (
        (frame.width, frame.height) != (SUPPORTED_FRAME_WIDTH, SUPPORTED_FRAME_HEIGHT)
        or frame.pixel_format is not PixelFormat.BGRA8888
        or len(guarded.slots) != 28
    ):
        return None
    layout = InventoryGridLayout(
        SUPPORTED_PROFILE_ID, SUPPORTED_COLUMN_STRIDE, SUPPORTED_ROW_STRIDE,
    )
    slots = layout.all_slot_regions(Region(*SUPPORTED_REGION))
    count = 0
    saw_empty = False
    for index, (decision, slot) in enumerate(zip(guarded.slots, slots, strict=True)):
        if decision.index != index:
            return None
        if decision.raw_v1_state is SlotOccupancy.EMPTY and decision.raw_v1_confidence >= 0.8:
            saw_empty = True
        elif (
            decision.raw_v1_state is SlotOccupancy.OCCUPIED
            and not saw_empty
            and _positive_iron_slot(frame, slot)
        ):
            count += 1
        else:
            return None
    return count if count else None
