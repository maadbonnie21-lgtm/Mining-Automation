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
    (61, 53, 41),  # observed at slot 9 background edge in the real 18-ore frame
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

# Additional source-proven row variants: mining-to-full-20260905-234057-2cfcfe4b,
# frame 00102-iteration-13-passive-03.bgra, SHA256
# 3e02ce10ac02c48da50f548fdcb7477f806de34a2e9ac41b3454f814725936a0.
# The image was visually verified to contain 18 iron ores; original and cropped
# regression evidence are retained. Unknown items are never learned at runtime.
IRON_TEMPLATE_SHA256: Final[frozenset[str]] = frozenset((
    "76ae2e3cb0b47ee5352b22f32bb93404211a955971874c5b6d40f115c5110349",
    "21277a9c9f4a95043c62da1d521cfe704c38137b52052bab76ee8fa38bb325dd",
    "29e5339c767ceca5e69477c487d7ed34eaa0065fbb7a5e21727d6a164229e973",
    "afec9102fd6fb292b1cb13a80177832cbce6ee043481f6f66fe636da23bade0b",
    "256dd5781334253bdad1f20f559ab05f3e17f97bf0aeec0ef9b4fe78a32e0c5f",
    "0d28c7b1ca5466bf63a5838d85ca62d6a2200d811a5e33a1f6150261ac64b25d",
    "52bcc369558fdfd562bafec91d9e7d062952845efbff474a0090391dc33dd158",
    "d4473bd347b531e0e3a1919dd1e192f51d0f325b7013cdc90312915775f8aeef",
    "73809fcf2bdffc9802a5ddbe9bd1972df90ff18b8c56257ddf954b1ec29210c5",
    "59c6247547b4df45cd8e66a301e2225f919e2ea9877c01084c25edbe0814dbbb",
    "ca696822add19b327cc6b8bc6630012f43488db980f56b23db0cd0ad70690764",
    "1fedf03b9138fe4a0ac8bff4ef82ece8d9a61f438f56c78714c9d32d838975fb",
    "29e01ade8e65777e0ab9ae763dcd862ef8384889183e7ea8cdb6fad495c846cb",
    "67674d6d9bf7a94d31a84e9e65ba5b7eed01c2a60ceb98cd1dbb38197ba4ec1f",
    "f7842be8bb95536cc5c0897e66fe5b5ad2dc7cff6ba0fad23318846024983c0d",
    "b46aeb69d0f0a1a7ca0ebe84b60b77155c1244692b5f58a79dccb9de7f341ef6",
    "380c54651b5b1a16a1ca8947f70a85af209ae86c53f6dce55c9b8e74adcd0507",
    "94eefc66c2ac80dc2be83129b0178617026fa6a0eb0635c5892565657b720d8d",
))

# Compare every foreground pixel, not aggregate color or a changed-pixel count.
# These limits are unchanged from the independent five-ore regression.
_MAX_IRON_CHANNEL_DELTA: Final[int] = 3
_MAX_IRON_TOTAL_DELTA: Final[int] = 799  # 0.40 * 1998 foreground channels
_IRON_REFERENCE_BYTES: Final[bytes] = zlib.decompress(bytes.fromhex(
    "78daed9d87771b4796af77a4370440129104932c921201344824e608068962ce4112a3826dc916255bb2e52c"
    "59b667676667bc335ebf9dddf7f7be5fd5edaeae8e68a4b13d473c7d7c744cf4adee0ab7beaefe50fcb77ffb"
    "d7ff89049bfcbe061c1385443d82afcc648fd727ceb7a75ba3412a42945865b988b330deff6077e6e2f8e677"
    "4f7763e1e65c36130a368ff45d9956bac5116e6aacac085cdbcee2d093a31b08fee74f0e11bfa1c157e8edbc"
    "91bdbe5248ed8ef51f4de73786d28dbe06943b3b365041fcb3cd290a4ef1fbbb5a1732d7107c7bb4ff64267f"
    "3e3bb8373ed0e4f365b359dc97a8b1716fb7834f7e727f0591afbfd386e0972e5d1eedbbb2524822f8dda9ec"
    "d9ec20e2df9ecc203eee6be7e6d0e33b0b1f9d2ebe385f6a6f097b2902b58a4fb2cb4ea771850832a574af0e"
    "a6f6c7074e8b85731eff782687eb9e1a4c3cbe3dffed936d5ccc8f9fdd79b857446730151136f60a3a06fb7b"
    "f3d9ccf0f0f0cc68d6eff3cdf55fdb63d59e45d8137e9ccd16827edf6a31f7e4ee8d3f7cb4f7f72f8efee7d5"
    "d99f5e1c506790838fe7aedd98e85f9ace6c2e147617870e97478e37265a22c1b17c126d870ffcee77976ee5"
    "127779701c6de1665c398e4bbfbbb4bf348c3ef6d74f6f23f8fffbf6fce7af4f70fdb865117c78a0fb6c73e2"
    "fdc3d90fef2e3c3d59fce4fef24767b79e9d2c869b1ba94f263ba3c5746facb99162e2482b4a96ffa0de3040"
    "be7eb4f17fbf3e41701cfffdcdc9b71f6eb7b746453bce8e241eee4d3f3dbe89b008fee5fbeb9f3e58f9f87c"
    "291c6c44049fcf5fe86edf1c4ea3711706ae071a1a52a914551a6e0df5766f77e6cf2f0efef10d8bffbfafcf"
    "7efeea38d1dd8e56a341343d945c9fcbbf7f304b57fed9c3d5d71f6e7df1de1a8ac06f293e7aced648ffc1f8"
    "000e9482ba19cba7c4e57d747aeb2f9fdefef9ab93ff7d7dfe3faf4e533d1d088e0ba0dfa2ce7716875133ec"
    "e21face04ed1525f3fdefcfcdd35117f2ad5cdbae574f6683a87636328150d36d1b065f1cf6ea14d71d988ff"
    "dfdf9cb64643380b7787d3674752e8ba07cba317fce2bf787ffdbb8b9dfff8e4367ac2ab0fb62221353ef200"
    "e223f259b1809e8fffee8ca663bccb15d2bd6dbc1bbf79b28dca49f5aa175f6455d7b03a9bbfbb367eba35f9"
    "fc7c896ae68fcf0f7efaf2188d85cf47b5f8e839a819167f76f0c1fc107a3e3a27ee62ad90c08da008740334"
    "28ae9c82e3e2e9d6d015efef4ebf77507c718f35eb1f9eedfdedf3a3fffaf218c7f74f7744fca57ce260428b"
    "bf307c8fc7bf3d39100f35a17fb2018c3ec04b11c129feddd5b10feecca3e7bce417ffd797b77fe2c1513f68"
    "05dc35e2a3297191eb8349537c0c81cc95f864eaaa9a489b1b31588a52a243fcf3ad69ea9368d63fbf38fce9"
    "8ba3bf7f7ef76ffcf8d3f3038a4f35d9d8d0b03d9a16f1dbc3410c8ab1eb5d48a78bb9be3b53390c645322c5"
    "59ef1ecca2dad155fef0f1fe7f7e7607619140fefa921d7ff9f40ec62f6e1d3dfccedaf80777173a5bc2483e"
    "88df1109a25a302866d23de8ae8793198c68c4a78b91e37f70f7c6378f377e78b6f7e3cb3b3fbe54235fbfda"
    "86c8382e5fbabc319f47befaf2bdb56f9f6ca139902d83013f062fcac5a058ce277946ca39c547467d73b18d"
    "0e2947160913c1f181af1e6d7cf774e787676c0ac3c8c5afd0886850a43b1a149491d063a3c166396d22fea7"
    "f75729b81c19a70783a1b5d9dc332d38860f65451a38bc457c2b0535ddb56bb90ef735323c249a80323c5585"
    "882c4e3fdb9afae6f1e6f73c38350a3e266a6082f77c53ae933ba728028d2e4716b7767f77063d16d582f8ff"
    "f919eb51727cdb738b76b3f02cffffa65f51d77afd448d8fe18041819e166f89c8356c7baec7c9f7d121a6bc"
    "ad7fff681fb9f1a72f8e911efbaeb6a5d369b9862bfea1ae8b2915f1312e901bffc1126390dab77a7243ddc6"
    "63217478f419547b2b8e685024c69ac0e1b8d67ce2c7da3daafc11cd57acb41dfff5f93fa482e578bef6fc1f"
    "0d352fcf6488ffe3d1101581495966bc8acb45f085f1f483dd69e27ff45274a770283872ddc0ff91e6c6ca8a"
    "08f841c583004b963f3f39a4b9266fe07f4cf169cc08b9ec4005231af14f372729f89f797cf0fffc8089ff33"
    "689e4c268bfb2ab7c6101f5081e0483b18c59739ff2febfc5f20fe67cddfe0dbbec1f81ff30538a7a335e2a5"
    "88894292f83f2df83fd5bd5a30f37f30e09b2c241e81ff3fdc92f83f642ac2d42b30bf7036eb11fc8fdb21fe"
    "bf6be4ff50c0bf52cc7d68e47fde1906e4e0e3b95e2bff23a711ff631498f81f7c22f87fefd6f0c59181ff91"
    "1bd12822f8d040f729f8ffc0ccff9120e3ff703068e57f459b137d3edfd1dab895ff3be231d18ec5e1c4c35d"
    "1bfe17fc9cef6edf18566cf91fa79fef4c9bf83fd9d39126fe0f35e1e9cc91ff35fe1c61fc9f16fc1f0982ff"
    "93e2f270a732ff2bbd9d698dfff1db5b538cff3fb823f1ff3395ff05df5af91f704e3d84e2a34dff4be3ff36"
    "5ef98cff434dc591d436f1fff14d2bff4739f088f826fe6f0911fff75037469da372946beac563b20880918a"
    "39e2ff8fedf83fa6c55fccf6c9fc7f4fe2ff18e3ff1eceff313187d23487f8c8301aff2fa1597f60fc7f57e5"
    "ff673b223e7bbeb0e37f20bac4ff3d26fe47fcdb2ba3e07f54cecb079cff3f35f07f4b84f37f81f1ff9a85ff"
    "81850312ff472cfc8ff8679b532efc2f800d8dc8f95f11f181e866fe0ff84d8914f1912b34fedfb3f23fea93"
    "f8ff361e73c0ffade1bd31c6ff9dd110aa257f95f1ffb2e0ff801fddc674fd487a8cff9f1af81f4fc73affcf"
    "e5700d5f70fe477334f91a10078317c37644e7ffac537c8c29ceff8772649130119cf3ff3af13f9a239954f9"
    "dfeff7cfa6edf85f4a9b88ffe2de0af1bf1cd9c8ffeb82fff1011a38d422cb7935dda12d4452b2f07f84aa22"
    "6de47f3e7981ff3764fe4773881a98e03ddf94ebbcf33fe2dfdb31f3bf1cbf4afe47fc87fbb322bee0ffb6d6"
    "a85cc315f33fe2bf7f3867e27fb4513aadd484ff111f5d97f8ff478dffe35a62ac09ff031aeacaff136ff9ff"
    "d7b4fe5f0ffe47701017f13f666d2a221a6aae09ff23f8fc98e0ff1db62a981900270fd78effb76ee8fc8fc1"
    "e5c4ff28b732fe3fded0f91ff1d38efc9f91f97f2cdfe7313ea005c1917688ff4734febf23f13ff81cf7853b"
    "15fcdf198f782982cd0ead11ceff0a510a50c1caffe0f3c9421f5b0c21fe7f79e7ddfd59dcaca98888b157d0"
    "61e27f4c88b6fcbf3c9305ffff00feff5ce57fc447a3c8c1c7b236fc8f8f8d4afc0f8a13b3ad98130112f83c"
    "f8ff2f1aff83fd408c6814115ce7ff3b06fe677ccbfb6402fcaff4b6343749fc9fd2f8df0fc2c4346de0ff27"
    "db9d6d2d12fff7513f37f13f86122ec3ef0fe4afb609fe6ff435802264fe3fdb36f33f4774850623f1ff7be0"
    "7f8ed032ffa37129be89ffc121a31aff37fa7d3891f3ff31f17ffa5a17820bfe5f9c1cd0f9ffbe81ff457cf4"
    "1c33ff8754fe67f14f6ecafc4f958fbbc3c51787ddf89f030f8befc0ffaccbe535fec7e75139fdd7d58b2f8e"
    "6570f16efc7fb12de2df74e1ff50539e77e3f638e37f0aaef2bf1fcfdd2efcbfdbaac55f74e0ff7689fff3d2"
    "60113de7f6ca880bff73a0ca80ff638cff13a6f818028cff933aff8fe68cfccf573004ffa38d4cfc1f8f0248"
    "32a8c946ceff5b233aff770afecf10ff67319031584cf11fb8f23fe564f0ffe10ae3ffaed6c82ee7ff2e95ff"
    "db64fe0f313e671723c77f747bdecaff783a16fcbf3eabf33f9a43e3ff143d14cbfc6f1b1f352ff33f451609"
    "735d20fa05e37f34473299d4f83f504cf7d25a8de0ff58283826f3bfdff7fc7c99f85f8e8cd343a1f06a312b"
    "8213ff733ecc48fcdf67e6ff540a742a033632bcc6ff8a89ff4f36264dfc4ff1a9060cfc9f4a95cbffe83060"
    "865746fe6f93e2d3b9f94af9bf91772d2bffb7b746c76ac1ff88ffeebeccff47e07fb6eaa2d486ff1b79d7a5"
    "f882ff4562ac0dffb798f95f24c6df0affcf8de770cc8e65def2bfe32a7ab899afb5fa26b417fab50dbe343d"
    "a0f27f4b782cd7a795a8331efdcfca82cf8da5ef6bfc4fc8c7f9bf4be6ff68736365453406fc5b0b0595ff5f"
    "1ce2fa31d7e42cfc8f9bc966fa2b4911013f6a862767c6ff889feeb4e17f4c370303e0ff50b93586f85c9c38"
    "44da21fe47cd58f91fd316ee6b7341e7ffaeb6a89722260ac90ee27f459dce2692c4fffd32ff871bfd13f9be"
    "f70fe75e4bfc2f3a83b11f9af99f214d2ec3734e1eb78309d1ccffc542a431b0346de6ff36d619fae5e0b6fc"
    "8f8f8de612c8c6d17010fc0f8a13b36da7c4ff3b3739ff7fa2f33f6e1c8d22820ff65f3dd918b7f23f5bbf25"
    "feefe0fc1f94f83fa5f3ff9dd53123ff9f82ffbbda5b443bce0cd9f3bfe0e71cf87f48e57f3c90822204ffe3"
    "f4d3ada93f19f93f7dad13ad46173f59b0e57f56444b448d8f9e63e4ff2050505cde93a31b32ff33845654fe"
    "c76f6f3af3bfe067f41c13ffb7849aa887507c8dffcf503954f98cffc3cd33c3c9ed1b8cff9fd8f13ff1ad78"
    "be30f17f2bef7279a5871e63bf79bc89f8037d57e8e2895af168a9f1ff2d8dfff77ffaf288f85fc407c45af8"
    "3f4ffc8f1b411100950e0bff233e328c0bff133f33fec7f385e0fff9e17b732c3e066f4738a8f3bf62e67fc4"
    "473f77e2ff7fff688f808df17fa869b59030c5ef646f2475fe8f06c1ff0939d135b2158c0999ff111687e07f"
    "016c6844f0ff26f13f8f0f444f7648fc3f990d37064c8914f1efefcea0da3f03ff7fb4f7a385ff292733fe5f"
    "1e4507bb128fee8ef623fe955818d59263fcdf0d443f9cc8502242b791e9a5912fd25af93fd5dba9f2ffe5cb"
    "6bb359c1ff680e95ff538cff31289624feb78d8fae2bf33f45160913c165fe477308fe0f0402334aeff68881"
    "ff5bc0ff52da44fc8f4f9728b81c99f87f05fc7fa206c7432bcf8a61c1ff6891a59c9aee3aa38efcdf21f85f"
    "31f03f4d5e5f73fe47706a9436ce9f82ffd1f3bdf07fde96ff03feb32df0ffa6ccffed52fc6af93fe097fd22"
    "c1ff18a4720d57ceff013f7bbf60e47fb411f17ff5208af8ef1f9af91f8df596ffe59fe9e1341dc5d1b7fcef"
    "e4ff34057c3e1c75f1ffb9ff73b43e7eb635d51a0da9febf566295e59afc1f52bec3c16693ff136eaed0ffe7"
    "6fe787c4fa3ff9ff96f57fa562ff9f5ea18af57f7bff7f4cf8ffc1726b0cf1815be4ffd05a968bffbf7d73e8"
    "d1ed79cc171f73ffdf4b11930585660783ff63e7ff833075ffffe59d077b33e2cb207257c1059b0e596916fe"
    "ffdd52feff1f9f9bfd7ff27f16c6fb6f4d6536e60bcc185f1a46b764fe4f215574f07f50e398eb4bfaffb2ff"
    "f301f1ff3d83ff0ffe4f6aebffb85a1e567f276eebff835ddb5b6365faff0a50679ef93fbf17fe4fd1e0ff9f"
    "0afe4f49fecff46079feff32f93f0547ff5ff67fecfdff6725fcfff5a164cc8bff1f62feff7645feff365b4d"
    "6de27dac872467f2ff65ffc7bbffbf68e7ffe32e5625ff0768011ef6e0ffdff5eeffb785d8fa3f7f58b6f17f"
    "84ff7f51c2ff4f32ff7fc8c1ff495e45ebe088d8fbff535efd7f9fc1ffb7f17f6cfdfffd5995ffedd6ffc9c9"
    "64af3956c71edfd1fdffce4848f8ffcb5efc7f23fff775b76380e320ffe781c6ff510eb4a12afc7f8aece4ff"
    "b8fbffdbccff095afc7fd5ff91233bf93fcefe7f50242579757a3ccff89faa42449e1aee0750b12faf6d99fd"
    "1fd9cfc7b9e8f994ee5cfc1f7c4cacff5364effebf7cae3bff130496f4ff5f3e5889b744260ac93af9ff35f4"
    "7feaedffa36e795664f08fc75e3c992277a56bebffb3e61bc864b2e278ebfffc22fe3f51655dfc1ff0ffb4ca"
    "ff71c9ff91f9bf72ff27d42cfb3fc2ffafa1ffb3fdcff5ffd3aefe7fb935a6fbff5c0d75f27fc8ffdfba3128"
    "f8dfb3ff9fd0f85f29e5ff1bfc9f877bc5b88dff6fe81574c848538dff1fe5fe0f9e1605ffef73fec76510d2"
    "58f91f7c22f8dfddff47f0a17ec6ffef59f85ff5ffb9ff33a3f418f85f5b1343bd1d59fc9fd71f6c59fdff8b"
    "52febfc6ff0d62fd9fded49f5bfc9f94adff5323ff9fbd5c90fc9f0afc7fc6ff21c9ff3f75f2ff4bf83f2efe"
    "ff36f77f109cf93fbc1b93ff2fe4a5eafd7f95ffb9ff23f8dfe0fff85886a9c6ff27ff87f83f5fb1ff9f674f"
    "5bd6efff0affc789ff4dfeff9fdcfd7f9fc1ffef34f37fd6c5ffffccd5ffe79a13e3ffaed6c89ec1ffa9d0ff"
    "4ff67408fe5f9fd5f99ff9ff9cff15258572bdf9ff66ff87d65169e0af4b88febdeaff2725ffbfd7c4ffb150"
    "d0ceff3f3445e6ebff21ddfff1e0ff8ba4e4ccff8ac5ff9f74f1ff0dfcefbcfe6f6578effebf77fef7e8ff83"
    "ffdbebe9ffd7d0ffa9b7ff3f5948f1af7e98f85f79cbffff623fb17090d65aebc1ff08bea4f17f5b2cacf27f"
    "b836fc8fe07316ff3f523bfe6ff4fb75ffff85eaffe76ae7ff23fef186eeff30ffbfd3c5ff0f891af3e8ff23"
    "3ef3ffb9ffe3c2ffaaffbfc0f8ff29e7ffce78d44b1193834a473c2abfce76f2ff27f28cff5f4bfcdf66f1ff"
    "0d4f85dafaff60fa1a290dccffe15f88b3fa3f5efc7ff27facfc8f3e39ca9186fc9f45a3ff23f8dfe4ffff6c"
    "f4ff85ff63e57ff2ff23dcff01ffb7066df8dfd6ff07ffcbfeff8c37ff9ff8bfd1c2ff67dbd316ffa74bf77f"
    "2af1ff8376feff89d5ffa9ccff0739b778f1ffc9ffb1e7ffbb5efc7fc6ff4a0f3dc692ff2ffb3f55fbff8cff"
    "c9ffc14cd7116f31f37f09ff7fc7a3ffef6fe0fccf4b31fbffcb1efcff3cf37fd64af17f34d864f2ff1b2dfe"
    "bfc9ff31f8ff3ea3ff6ff27fecfc7f0cf90782ffedfc1fcac901a6398d3e66fe8fd5ffef76f1ff11dfd6ff4f"
    "f56afccffd9f0706ff9ff37fca93ff8ff826ff9f220bff5ff67fbeb7f5ff25ff67dbe2ff233ef7ff0f4d919d"
    "fc1f1bff9ffc1f23ffeb805d70e3ff938d4993ff23fbffe3bce797e67f26810cd8f27f49ffdf7a6e59fc6ff5"
    "ff5f3e5c6d8fc7c66ac4ff56ff5ff83f35e1ffbafaffa3d96b36ebff4a2dfd1f1481f1fe96ff7f71ffbf2caa"
    "2c37f8ada981a335c6ffe84e5444ccc8ff55f9ffa38aeaff5fa8fe7f241c1aba66f4ff834dd5faff1792ffdf"
    "d3515bffff8d37ff1ff7256a6c2473ad2cff1f5303b9ac6efeff7c41f07f573c3aeca108b6fe1f8fc8fcefe4"
    "ff8f9bfd7fceffc646519f438debff1c69d425cd6afc7f041fcdf468fc9fd7f9bf254c4863f5ff811082ffdd"
    "fd7f0497f9ffe264f1c53d83ff8fb6b3f27fcad5ff475d55e0ffabfcef6b482492b2a96bf1ff4f08d1693056"
    "e0ff83435cfc7f81d055f8ffc9d650b327ff7fc8c1ffff9cf1bfe06727ff9ff37f37f13ff9ff99849dffbf59"
    "a1ff0ffec78da008800a1ee8c41c5a33ff3fa2faffe0ff9c85ff11ff6069e47149ff9ff17fb38dff1f55fd7f"
    "89ffedfdff17aefeffdc588ef9ff3eb3ff9fa891ff8f463ce0fcff4e8dfc7fe55aa7e0ffd562f6c1ee8ce4ff"
    "ebfcefd1ffff56e27f8a2c12a6fc15ddefb9ff8f91ebe4ffb337566147ff5f8e8cd3c3e1f0ca4c160fce5efc"
    "7f2dd7b1a464e2ffce7854e57f07ffff3b37ffbf99d25d4ae2ffe258de22810f54ecff9bce454ffb35fbffca"
    "b5df8cff4faf7eeacaff6fd7ff7f2dfe0f5f6bad97ff3f9dc1047ab63915d7fd7f83e95df9feff46ff47f5ff"
    "f9fe9f346189d7d695efff6ff4ff7d3eb3ffb35e85ff8ff826ff9ff93f0efe3ff37fcaac31c4c7bc4cfe0fbd"
    "cb1e35acffebfe3f7eb66e0c229f60bec06cdede1af15204c62f9f1dd89226d28eabffdff7f8b6eeff20f18a"
    "ce50caffefc965064afaff2ba5fd7f75fd7f7172607d3ebf7d7370ffd630ba252e9efbff195bff07137dc893"
    "ffafeeff63e2ffa792ffaffa3fe07f1e932b04acd2a8de30937e65f4ffc1a2edf1b2fd7fd0c87cff757fc3ef"
    "c5fe3fb835e6ffef58fc7fcdff8968fe8f47ff1f8d0ba072f7ff65ff47f7ffef94e1ffaf0d7af4ff25ffc7e4"
    "ff33fedf74f3ff4714e1fff345ced09b273b26ffa71cffbfcfc6ff1f4cae08ff3f03fe8f0160e4f57ffa0b23"
    "d5f8ffbaffe3e7fe4f66a002ff7f94fc1f07ff7fc2e0ffa4e4d9bf2cffbf09375b07ff1f9543fecfa3dbf35d"
    "c2ff8f56e5ff27ba81818c79c8ffb9aff13ff9ffe07cc5b3ff0fbcff56f2ff29b2489826ff07d585918b5f91"
    "ff3f6bf4ffb7461470ba9dff7f688aece4ff48fe7f46f6ff45aeb3f57f08ff44e4d9b1fcdc784edbffbf94ff"
    "6fcc75b6fe0f5f04d6230bb5c98bff2fcea59bb245530e8119d3a301e2bfbb6ff6ff3fb9bfd2d612b5f8ff99"
    "0a8817f1adfe7f2dfd1f9f4ff8ff3fd665ffff14cb8aec6bbf8cfc91b844eeaad9fe9f830ae77ffda79efbff"
    "642a6bc77ffdf57f8dbbeab4ffff12e77f80aeeeff870da457cdfeff56ff9ffc1f13ffd7d0ffcfd5d4ff3fd9"
    "98642f1784ffdfe9eaff9759636c7738eeff27b81a5ac2ff5fd0f9dfa3ffaff3bf22f1bf9dff4ffe8fe0ff07"
    "e0ff98d9ff37f50a3a64a4b1f7ffc9ffe1febf58ffffe3738bff1fb6e77fbe0d691269011f50f95ff27f04ff"
    "97f0ff55ffc786ffb9ffaffb3f06fe4fa504ff5bfdff578f372bf0ff89ff8dfe7fc6d6ff57e4fd7f9cfd1fab"
    "ff4ffc1f0d0665ffffa9d1ff175b68daf8fff7bdf9ff83baffe3e2ff230338f93f7fe3ebff9efc7f45e57ff2"
    "ffa5fd3f25ff7fb322ff9ff3bfeaff30fe6f31f17f2dfd7fbf9f4ab1faff8f4bf9ffa3aefebfceff41f2ff33"
    "32849c1afc7ff3fabfecff3719bfffabfb3f1efd7fbbf57fa435e2ff43e27fb3ffaff93f13e5f9ffa95e8dff"
    "99ffa3f37f8cff9d2cc6ffa932fc7f409aecffc8fcbf26edff29fc7f0221d5ff97d6ffb7d813abd5ffd7fd1f"
    "23ff1bfd1fb3ff9f51fd7fcdff71e27f647813ff4f0d0f14470dfeff1f1dfc7fe6ff18739d3bffd38595e5ff"
    "7be77fd3af6cfd7f3c4cb5b7c62cfe7f45fc6fe7ffa38d7e2bfebffad58f7af2bf68befaf1bf68beb7fcefe6"
    "ffd48fffb9ff43fcdfd612ae31ff1bfd1fd5ff0f8786af77c9fc1fd5fe9050f97ebeb6ffff85e4ff5bfc9f2a"
    "fc7f9fecffb8fbffccff29b3c6d8dbe1b325f27fe85db68bffbfb95000ff5f70fe47cef7caff6d3affbbf8ff"
    "e346ff1ffc2f3a4309fe570cfcefc5ffff07e7ff78d4ecff8f667bacfcdfc6f7ffe7fc6ff1ffa321c1ff65f9"
    "ffa843c1ffaaff2ffb3f16feb7f5ff5f7db0d925fbff431efc7ff27ffa99ff2ff93f195bff5ff83fcefb7fda"
    "fbff2aff870cfeff85d1ff97fd9f0afd7fc6ffcd9efc7fcdffb1e57f17ff7f5bf77f7a3ae28cff5f73ff3f63"
    "ebff1bf85ff77fdcfdfff521e2ff6603ff2bb5f4ffc9ff21fecf39f8ff8f4bf9ff8cff43cd56ff9ff6ff9c90"
    "fd1f13ff6bfeff0b0ffe7f93d1ffb7fa3fe4ffcf5af6ff7fe9eafffb7caaff03febf1257f99ffc1f13ff7bdf"
    "ff5fe9ed14fcbf5acc0afe6fe57b0213ffd343f19287fdff65fea7c822619afc1f340746aec6ff16ff7f4461"
    "fe8fcdfeff87a6c84efe8fe4ffb3bebd24fc7f2dd771ff67d09effb5c8389d095d7e3ff9ffdf39f9ff79eeff"
    "54caff885fdaffaf82ffe9ab2556fec743544df81ff1dfdd9f35f9ffe96b9d7c87a5c15aecffefafabff3f69"
    "bbfe5f53ff0745e432ff8cf5ffb9893c3bf8bfdf02bfe9477cffb71efe3f820bfe97fcffa0cc7815fbff8833"
    "0bfedf99069bbd31faff26feafc6ff0758be31faff0b999af9ff4746ff5f11fc3f62e7ff975963c2ff273554"
    "f5fff34904b7faff1bf3e0ff39e27f8ffbff234f7671fe57b4e9ccd1ffcf5d97fdff877bc576cbfeffa65e41"
    "478e8bd365f9ffff7875eae2ff33fe9f93f85ff5ff3331be8fbac9ff57f9ffd2a59d9b83b2ffffb3d5ff4fdb"
    "f33ff9ff51ceffd329f07fb3e0ff145fe8a37ab3faff60d7aef6d64afcff7ef2ff13624e54fdffe706ff9f23"
    "7a8a2e7ea2d0b7e6c0ff56ff9ff83f160a8e38fbff42a1afd8ff073903ce4bfaffb8f86993ffff9e81ff5dfc"
    "7ff07f9cf33ffa5827e3ffe02beeff6713efd8fbff6765fbffc4ffb8919ce6ff9bf8bf7affbfd3c0ffdd9efc"
    "ff2f74fe27601bcda75aecfc7f0c81feaed609f3f77f33a6158c92fefff4483ff1bfecff5f8919f8ffb693ff"
    "bf33f3d283ffbfbfc4f8ff9d36d5ff7f07fc7f49f7ff0f4af9ffdf1bf93f7dbdcb96ffe3fc4f57224e4af2ff"
    "774bfaff7cdf6339b281ff4f4cfe7f8240c8ceff07ff87c672095bff5f8eccf93fb23c9371f6ff595ab825f9"
    "ff2229814e8ba5f89f4e77f7ff19ff87d57427729dfdfe3fb6fcefc1ffb79e5b06ffdbf9ffc8789d6d31b986"
    "2be77f3bffbfff7a97a2a4e41afe55fbfff1faf2bf68beb7ebffbfacffc33667a88fff4ffe0ff00613287208"
    "15214aacb2dca8c5ffcf3bf83fb5f3fffd36fe7f4343be1aff5fe37fe6ff5bd7ff35ff1ff7556e8d31ffffde"
    "32edff432eabb3ff8f279dc1f70ee6305b613eeaf0e6ffe3690efc2fa60617ff9ff93fb2ffbf332d3a83b51f"
    "ca473edd9375f7ff8b46ff5f5dffdf37f9ffb4ff277a8bcaff3706f76e0ddd5d1d8b6bfebfeeff18fd7fcce9"
    "36feff5706ffdfb4ffa7ccffe1e646ea93c4ff2dc1a6909dffc3fd7fc3faffd78f74ffc7a3ffcffd9f04aa88"
    "fbff4977ff5ff77f5cfdffb09dff8f5222c1a09bff2fedff53b9ff1f2aedff479dfd7fd0c237aefeffd688d2"
    "c2bb1cfa18f81f1d92fc7fd9ffd1fd7f07ffc7ddffc75dace4fb68ff4f74e3aeb6168cac7af8ff017cc21fc8"
    "2bfa60114d736755f37f1edaacffabfe7fcedeff17fecf64aa7b92f93f4deefebffaf77f25fe97fd7fe401d9"
    "ffef8c98fd9fe64afdff46bfff70650c38d4d51ad91fcf08ff47f7ff27caf3ff913081ca38c8ffc1f011fe7f"
    "90f17f80fc1faffeff13c3fe9f08ebe4ff70ff3f69f0ff470cfe7fccd9ff4ff576b47142b3f77f9edbf9ff92"
    "ff834447ef35e4d5693c8023c323ac1c599c7eb6c9fc7f99ff653f1fe7b6849a425a5827c0c3c76cd7ffbdec"
    "ff6f3dd716f08a760c4f5dcbecffdf5b6e6b35f8ffc54af9df76ff7fcdff19acedfefff5f0ff51b7f4d50f82"
    "7ffc5be4ae5af93fa2f9eac7ffc5b7fcefc1a221d61a2fd4c5ff599a1a10fc4f458812d572abf37f8cfe3fed"
    "ff591bff47f7ff39a2d35c93eba9b1ff2fd6ff1dfcff01f2ff23e150b935a6faffdcffa1b52c27ff1ff7b539"
    "5f10fc0f1cf2b4ffffa0d2d51693f97fd2c1ff1f37fafff777ecfd7f767701bf7c14d2d7b265faffc4ff56ff"
    "7f34db63e27f74cb782c4c4843fcbf28fbff7c4d8cf8df9bff6fb3feaffaffe1a0b6fe6fcfff56ffff9bc79b"
    "1d92ffe3d9ff67fc1fb0f83fe7d6fd3fbdf93fb6fe3f4a89b27d08536efebf529eff3f69e17fe1ffb8f9ffa5"
    "fc1f17ff1f34d5cabb1cb099f89ffc7f69ff4f17ffffc8ddff97f99ffc1fc6ffed9cff955afaff92ff63c3ff"
    "5efdff1c7bda5a77f67f88ffd98b5407ffff8507ff1f7946f6ff75ff27abfa3f4efeffcb52fe3ff81f1d00fc"
    "7f85f1bf8dff7350a6ffaff476eafc5fcc0afe4773c8fc6ff27fbcf8ff145924cc35a3ff538dff8f277a99d2"
    "6dfd1f6a0ec9ffd7d7ff89ff4dfe0fadf0d8f23fa95fa6f57fe24f95ff79cf0f69611df93fdf5796ff6fe0ff"
    "7ce5fc6febff83ff4dfe7fb1a6febfb6ffcf60fdfcff9af17fbeaf236ee17fa5a6fc9f7fcbffbf0affa74a0e"
    "2fe9ff10ff0be55b945865b9baffc3f658d6fd7fabff53a5ffff46f3ff39ffd7d1ff573a5bacfe8ff0ffcbad"
    "31e1ff23eda8fc6fe7ffb0bf2be6f36f48fc8f9cefd1ffa7f57fa5a4ff9fbbfe88fc9f17eafe3f56ffdfd42b"
    "e8c829ddd9b2fdff53abff4ffe8f95ff99ff93d7fd9f458bffc3f8ff5209ffdfc5ff01dfe68cfe8fe0ff94c1"
    "ff1fb3f2bfd8ffd38bff4ffe0ff13ff7ff13aefebfc1ff9974f67f74ffdfe0ff24dcfd7fd9ffa9ccff5ff3e6"
    "ffcbfecf859dffe3e2ff0bfee7fe0febc6e4ffcbfe8fd1ffbf55aeff2ff89fba71577bab99ff6be9ff07e4c1"
    "52aeffdf62ebffb3fd3f5b27123affdbf8ff1bdefd7f9fecff5bfd9f4afd7fc6fffb4b8cffdf8947c9ffb7f5"
    "7f5cfcffef2dfb7f0afe5f65fc3f8de1f09afbff82ffd943b1d1fff1e4ff5fd3f9dfeaff70ff3fe1e4ffb337"
    "56e19093ff8f277a23ff4796a733aefebf61ff4f95ffb9ffe38dff4bf8ff3817e32ba4852d97ff1b3decffcf"
    "cfedaf8cff1bedf6ff470231f9ffc5cafd7f9f8dffaff93fb5f0ffebbbff3feab6d391ff33b5e3fffeb7fcff"
    "0bf37f44e5aebaf8ff9120667fe27fddff8f1848af72ff3f129c1d31f9fffd510bffc7aaf5ff1784ff8f8c9d"
    "15feff68ba0efebfceffc746ff3f1a09eb3596f7ecff9f32ff9fd4d0ffe3ecffe3bef80ab0ceff5e8a9828a4"
    "aeb4c738ffa7dcfdff31e1ffbf50bfff2b3a83b51fea47c05f507ae9fbbfecef7f95f4ff9fe9fc6ff6ff23c1"
    "113bfee797a1cc8de75a222193ff0f3e21febfacfaff371cfdff48b090be7abcaef1ffb1c5ff8f84fb38ffc7"
    "4312ffbbfaffe0ff2be5fbff2aff7bf0ff81e80af17f243891d7f9ff8527ff3fe1eeff6793ef207825feff94"
    "ceffccffcf97f2ff23c1e9c14485feff8812e78f9cb954373dc6be7accf83f9704ffa72af0fff7adfe3fe77f"
    "e6ffa764fe4fd5d2ff8f4afc9f32f3bfd5ffff8bbdff9f74f3ff13ef10ffc72af5ffa913eadfff55fdffb017"
    "ffff5e29ff5fe3ff11c6ff6d51d038e25f6d095f2ecfffdffdabc4fffd6069ceffc8992b3319f03ffaea6bdd"
    "ff0f60f056e6ff53649130572445bfa4ffaff2bf83ffdfdf7785cd204a4af0ffd2b457ff1fb94ee67f7981fd"
    "4a5b8cf85f44f6eeff7be3ff8489e1cbf2ff2be77f5bfffffe4a675bcb584df8dfd6ffeffbcdf8ff63d2573f"
    "24fe4fd594ff136ff9ff57e1ff70d762a250affd7f6eaf8c1d6f48febf566295e51afc9f0bcdff0f33ff6732"
    "79551c516d23f1cafd7f8ee8e4ff4bebffe99af8ff6f3cfaffe150b93526fcff546f0786f0e5cb6efeffe642"
    "e1ddfdd90f8f6e60caf0eaffe7125db4a5b9a260e2f3eeffdfdbb6f3ffc3cd4d8004e9c09d72a5a1dfc1ffcf"
    "e3f0eaffb3fd3fc1ffcacd8981b5b9dcd68dc2ee22f93f16ff5ff77f98138b870be6ffdf72f5ff4dfe0ff1ff"
    "a9e4ff4bfe0fcdb3e4c4a2d2a8deacfeff578f36acfeff85b3ff4ffe0fae9ff9ffbf2fedff5bfd9f32fcff5c"
    "09ffbfdfeaffdff4eeffb383fe6caed1ff3fb4f1ffe5bfff5b86ff5fc07f37c9ffe77d0c331dd085fc7fd9ff"
    "a9daff4f2cb3f5ff26eac678a0eb8c1bd6ffadfeff0f65faff1dfcbd15fbb27c20200f9672fd7f5ca4adffdf"
    "df151f07ff6b8994068bd5ff7f61f4ffffeec1ffef32edff39e9e8ffbf2ce9ff0798fff3dec1dc15cdff57fd"
    "1fe6ff4bfe8fb3ff2fd6ff7fd42c1d021e5aff3fdf56d7ffd11c78728f36e9feff5299fe3f45e6097300114c"
    "fbffa0ba1289247e65f0ffb58cb4399c8a8542b29d2efbff7264c9fff1e6ffb3f5ff007baeb1f1ff63eddada"
    "2f4596fdffaf4bf8ffcd3cd70578ae1b10a79b24f0cc40bf1cb92cffdf746e35fe3fc6c58bf3a5bafaff4aed"
    "f6fff927f8ff2c2b6af08fc42572570dfd7f6a3ef1f396ff7f09ffa78efc4ffe0ff17f5b2c4c4530d343e2ff"
    "6afd9f5dd5ff21e59bd6ff4dfc5fa5ffffc6e0ff6bfc3faaf27f0dfd7f9bfd7fc624ffbfcc1a0bf81a9e9f2f"
    "93ff43fcaffaff2336feffc6bcceff1efd7f8cdf2bed06fe77f4ff99ff332fd6ffefed4ca333d8f83fb83be3"
    "fa7f9e290dfd0efebfcaffdcffc919fd1fb3ffaff93f9cff678dfccffd7fd5ff91f89fde8933febf64f1ffbf"
    "32f8ff66ffe7d8ecff4bfe8fceff2989ffadfe3f68aab37cff9ff83f60f17fce9df7ff74f77f5cfcff5167ff"
    "dfb4ff27f3ff6f7af7ffd9e1d1ff47ee72f77f1cfcff82eeff04d8b639243990ff2ffdfd5f27ff7fdfbbffbf"
    "acfb3f36fc5fc2ff7fead5ff27fecf59f8dfbbff8f8b5c77f07f0cfc6fb3ffa78dffff770ffeff15a3ff7fdb"
    "d6fff797f2ff4dfc1f57fd7ff27f4afbff7ed5fffffee9aeccff783ab6e57f3407f17f4af2ff775dfc7fbfd9"
    "ffa7c806fe3f31f8ff18b904427ebfdfe4ff6c0e2bb1b0e16b53882ffc7f39b2d3fe3faefe7f80de6b98f89f"
    "65783bfe2fedffe7d89b2fe27f8475e1ffac1dff7bf2ff2de756e3ff33feafb3ffcff7ff4cfd36fcff7f0aff"
    "677fddfcffff0182f5f881"
))
_IRON_RGB_TEMPLATES: Final[tuple[bytes, ...]] = tuple(
    _IRON_REFERENCE_BYTES[i:i + 3072]
    for i in range(0, len(_IRON_REFERENCE_BYTES), 3072)
)
if len(_IRON_RGB_TEMPLATES) != len(IRON_TEMPLATE_SHA256) or any(
    hashlib.sha256(item).hexdigest() not in IRON_TEMPLATE_SHA256
    for item in _IRON_RGB_TEMPLATES
):
    raise ValueError("retained iron reference templates are corrupted")


# Exact full-slot RGB templates from the visually verified real 28-iron frame.
# Source: mining-to-full-20260905-235341-c3778539/00088-iteration-11-clean.bgra
# SHA256: c33abd9e1b0f7c02543d4d9ebcbad794b5a0727e466d5495bc7b50303430343b
# Slot-specific matching preserves even the original background and edge pixels.
_REAL_FULL_SLOT_SHA256: Final[dict[tuple[int, int], str]] = {
    (567, 569): "0cc7a3b195fd67e8b331dbf50680e1bf78f0402e44cae4d5ad62edb51043a579",
    (609, 569): "f362fc508102d25a00d0284399e8d1ab24019241a6bc1fc07024f33fdd560d94",
    (651, 569): "9957eaf6ce4b926ad62263bc324eb8aba81beefcb660a83caff386ee69017cee",
    (693, 569): "8f71e267d033ae274cf71a93886c268ada220f7227eaf098dd85294a1f07d375",
    (567, 605): "941337fc816bcfe527dc29c6731445d7b6ed6d4d8b85d3e6149b6554a89b5343",
    (609, 605): "ff9468873ceb07d0cecebc8b5fe7d57934e9b4088de4ecd89ded0b96a19efd8f",
    (651, 605): "62b0b671d858162ae998257d74d163480a630833d3c03a96a7a6b7b785bef45a",
    (693, 605): "c9f4c6fae6f0d97fb1692239a26a457a5976d0b7740a9fc685decfed1a6f5364",
    (567, 641): "7438f3dd1bda58bccc4167239970fe3eae8e8388d78e470cc12f1fe81058f7a0",
    (609, 641): "6b58224bdfb788197a96c1c8be83a9ba6c12036e838f1d114db9615340d08e5e",
    (651, 641): "26ac3d75032a518b18f4dce97ee9d6d8c1436a2af0c927f7d217ed2dbc964f67",
    (693, 641): "0e9b11ebb15731359cb492ba9800da50a15f0540280df8cd3a85085e3cf35ea1",
    (567, 677): "2b6f1b45bbef7f8430984971419d95a38c7d703d82deaaa4759f7ac78a2dd45f",
    (609, 677): "ada6f4bcf55ce6801f903954cbcff0c82411133300238b4ef858612572f44344",
    (651, 677): "d1e82137cf76bab88b12ff8fd1e01ee99beb9ec05a61144540087ea388efe7a9",
    (693, 677): "ddc27e73aa1a9689efa5d2764af10d7076cafb0a1accdae57ae8c88d00895be1",
    (567, 713): "12f155b4a7fb717e6bf7ca5d8f390900fe4091c755ccaa6209776abdde0d6b70",
    (609, 713): "5aa358eed9590d4c712a5b3c6ec259b09f6567894c73e8a248ada7d71e526c1c",
    (651, 713): "84ba6aa4cff26670c4b927f1b5e9dccb01c5f26d4d0190d460d8364e91a6c0f9",
    (693, 713): "695e25dfc2c8e77016dd01757c1fd318d99793af730c01ab0c31db8ce6082b20",
    (567, 749): "562ab3bb846c1de8e101b1ab6701389f9c3a32983f373657c6bd0535cf7fe702",
    (609, 749): "44920601ed421150bf78fbbab840d365ea702b2e2396a134a5ed677c621a35f4",
    (651, 749): "483184b01746d7000ec45068787390f35e9d8e2b16615acab571e11705394cbc",
    (693, 749): "0481f168a27f14470c4749210d8e138e4e855e4a3e473fd6137a9ab9a58c37d4",
    (567, 785): "f00cee35f50d403bd82d87ba44644e824e9d1f465aca74bdadbbb5a6e6039d0b",
    (609, 785): "fbb86e76eba7bb8980d427b287a3f9bffb8063214bfb6230a6f46344ea776506",
    (651, 785): "7bfec74780776f7f1cd340786c861cb8010c13e03e4965b1921f951e5f9555d1",
    (693, 785): "0d6a3368730325ba48c58969fa4cfb009fb2508ecd052c54e41865755330df47",
}

def _positive_iron_slot(frame: Frame, slot: Region) -> bool:
    expected_slot_hash = _REAL_FULL_SLOT_SHA256.get((slot.x, slot.y))
    if expected_slot_hash is not None:
        full_rgb = bytearray()
        for y in range(32):
            for x in range(32):
                offset = ((slot.y + y) * frame.width + slot.x + x) * 4
                blue, green, red = frame.payload[offset:offset + 3]
                full_rgb.extend((red, green, blue))
        if hashlib.sha256(full_rgb).hexdigest() == expected_slot_hash:
            return True
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
