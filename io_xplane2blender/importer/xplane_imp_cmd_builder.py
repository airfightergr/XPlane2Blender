"""
Statefully builds OBJ commands, including animations and materials.

Takes in OBJ directives and their parameters and outputs at the end Blender datablocks
"""
import collections
import itertools
import math
import pathlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from pprint import pprint
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

import bmesh
import bpy
from mathutils import Euler, Vector

from io_xplane2blender.tests import test_creation_helpers
from io_xplane2blender.tests.test_creation_helpers import DatablockInfo, ParentInfo
from io_xplane2blender.xplane_constants import (
    ANIM_TYPE_HIDE,
    ANIM_TYPE_SHOW,
    ANIM_TYPE_TRANSFORM,
)
from io_xplane2blender.xplane_helpers import (
    ExportableRoot,
    floatToStr,
    logger,
    vec_b_to_x,
    vec_x_to_b,
)


@dataclass
class IntermediateDataref:
    """
    Matches xplane_props.XPlaneDataref.

    Made since dataclasses are more flexible then bpy.types.PropertyGroups.
    """

    anim_type: str = ANIM_TYPE_TRANSFORM
    loop: float = 0.0
    path: str = ""
    show_hide_v1: float = 0
    show_hide_v2: float = 0
    values: List[float] = field(default_factory=list)


@dataclass
class IntermediateAnimation:
    """
    An animation is everything generated by one pair of ANIM_trans/rotate pair (or
    the static version). An IntermediateDatablock may have 0 or more of these.
    """

    locations: List[Vector] = field(default_factory=list)
    rotations: Dict[Vector, List[float]] = field(default_factory=list)
    xp_dataref: IntermediateDataref = IntermediateDataref()

    def apply_animation(self, bl_object: bpy.types.Object):
        def recompose_rotation(i) -> Vector:
            """Recomposes the OBJ's split form into one Vector"""

            tot_rot = Vector()
            tot_rot.x = self.rotations[Vector((1, 0, 0)).freeze()][i]
            tot_rot.y = self.rotations[Vector((0, 1, 0)).freeze()][i]
            tot_rot.z = self.rotations[Vector((0, 0, 1)).freeze()][i]
            # print("Pre-combine rotations")
            # pprint(self.rotations)
            """
            for axis, degrees in [
                (axis, degrees_list[i]) for axis, degrees_list in self.rotations.items()
            ]:
                if axis == Vector((1, 0, 0)):
                    tot_rot.x = degrees
                elif axis == Vector((0, 1, 0)):
                    tot_rot.y = degrees
                elif axis == Vector((0, 0, 1)):
                    tot_rot.z = degrees
                else:
                    assert False, f"problem axis: {axis}"
            """
            print("Recombined Rotation", tot_rot)
            return tot_rot

        current_frame = 1
        # TODO: Does only one locations list work for this? ... I think not?
        if self.xp_dataref.anim_type == ANIM_TYPE_TRANSFORM:
            keyframe_infos = []
            for i, value in enumerate(self.xp_dataref.values):
                keyframe_infos.append(
                    test_creation_helpers.KeyframeInfo(
                        idx=current_frame,
                        dataref_path=self.xp_dataref.path,
                        dataref_value=value,
                        dataref_anim_type=self.xp_dataref.anim_type,
                        location=self.locations[i] if self.locations else None,
                        rotation=recompose_rotation(i) if self.rotations else None,
                    )
                )
                current_frame += 1
        else:
            keyframe_infos = [
                test_creation_helpers.KeyframeInfo(
                    idx=1,
                    dataref_path=self.xp_dataref.path,
                    dataref_show_hide_v1=self.xp_dataref.show_hide_v1,
                    dataref_show_hide_v2=self.xp_dataref.show_hide_v2,
                    dataref_anim_type=self.xp_dataref.anim_type,
                )
            ]

        test_creation_helpers.set_animation_data(bl_object, keyframe_infos)
        current_frame = 1


@dataclass
class IntermediateDatablock:
    datablock_info: DatablockInfo
    # If Datablock is a MESH, these will correspond to (hopefully valid) entries in the idx table and _VT table
    start_idx: Optional[int]
    count: Optional[int]
    # At the start of each IntermediateDatablock's life, this is 0 or 1.
    # During finalization of the tree, they are combined.
    animations_to_apply: List[IntermediateAnimation]

    def build_mesh(self, vt_table: "VTTable") -> bpy.types.Mesh:
        start_idx = self.start_idx
        count = self.count
        mesh_idxes = vt_table.idxes[start_idx : start_idx + count]
        vertices = vt_table.vertices[start_idx : start_idx + count]
        # We reverse the faces to reverse the winding order
        faces: List[Tuple[int, int, int]] = [
            tuple(map(lambda i: i - start_idx, mesh_idxes[i : i + 3][::-1]))
            for i in range(0, len(mesh_idxes), 3)
        ]

        if self.datablock_info.parent_info.parent == "ROOT":
            # We never make ROOT, so we be sneaky and never parent it either
            self.datablock_info.parent_info = None
        ob = test_creation_helpers.create_datablock_mesh(self.datablock_info, "plane")

        # TODO: Change to "next mesh name"
        me = bpy.data.meshes.new(f"Mesh.{len(bpy.data.meshes):03}")
        me.from_pydata([(v.x, v.y, v.z) for v in vertices], [], faces)
        me.update(calc_edges=True)
        uv_layer = me.uv_layers.new()

        if not me.validate(verbose=True):
            for idx in set(itertools.chain.from_iterable(faces)):
                me.vertices[idx].normal = (
                    vertices[idx].nx,
                    vertices[idx].ny,
                    vertices[idx].nz,
                )
                uv_layer.data[idx].uv = vertices[idx].s, vertices[idx].t
        else:
            logger.error("Mesh was not valid, check stdout for more")

        ob.data = me
        test_creation_helpers.set_material(ob, "Material")
        return ob


@dataclass
class VT:
    """Where xyz, nxyz are in Blender coords"""

    x: float
    y: float
    z: float
    nx: float
    ny: float
    nz: float
    s: float
    t: float

    def __post_init__(self):
        for attr, factory in type(self).__annotations__.items():
            try:
                setattr(self, attr, factory(getattr(self, attr)))
            except ValueError:
                print(
                    f"Couldn't convert '{attr}''s value ({getattr(self, attr)}) with {factory}"
                )

    def __str__(self) -> str:
        def fmt(s):
            try:
                return floatToStr(float(s))
            except (TypeError, ValueError):
                return s

        return "\t".join(
            fmt(value)
            for attr, value in vars(self).items()
            if not attr.startswith("__")
        )


@dataclass
class VTTable:
    vertices: List[VT] = field(default_factory=list)
    idxes: List[int] = field(default_factory=list)


@dataclass
class _AnimBoneStackEntry:
    animation: IntermediateAnimation
    inter_datablock: Optional[IntermediateDatablock]


class ImpCommandBuilder:
    def __init__(self, filepath: Path):
        self.root_collection = test_creation_helpers.create_datablock_collection(
            pathlib.Path(filepath).stem
        )
        self.root_collection.xplane.is_exportable_collection = True
        # TODO: Hack!
        self.root_collection.xplane.name = "imp"
        self.vt_table = VTTable([], [])

        # Although we don't end up making this, it is useful for tree problems
        self.root_intermediate_datablock = IntermediateDatablock(
            datablock_info=DatablockInfo(
                datablock_type="EMPTY", name="ROOT", collection=self.root_collection
            ),
            start_idx=None,
            count=None,
            animations_to_apply=[],
        )

        # --- Animation Builder States ----------------------------------------
        # Instead of build at seperate parent/child relationship in Datablock info, we just save everything we make here
        self._blocks: List[IntermediateDatablock] = [self.root_intermediate_datablock]
        self._last_axis: Optional[Vector] = None
        self._anim_intermediate_stack = collections.deque()
        self._anim_count: Sequence[int] = collections.deque()
        # ---------------------------------------------------------------------

    def build_cmd(
        self, directive: str, *args: List[Union[float, int, str]], name_hint: str = ""
    ):
        """
        Given the directive and it's arguments, correctly handle each case.

        args must be every arg, in order, correctly typed, needed to build the command
        """

        def make_empty_as_needed() -> None:
            if (
                self._anim_intermediate_stack
                and not self._current_intermediate_datablock
            ):
                empt = IntermediateDatablock(
                    datablock_info=DatablockInfo(
                        "EMPTY",
                        "next_empty_name",  # self._next_empty_name(),
                        ParentInfo(
                            self._current_intermediate_datablock.datablock_info.name
                            if self._current_intermediate_datablock
                            else self.root_intermediate_datablock.datablock_info.name
                        ),
                        self.root_collection,
                    ),
                    start_idx=None,
                    count=None,
                    animations_to_apply=[self._current_animation],
                )
                self._current_intermediate_datablock = empt
                self._blocks.append(empt)

        if directive == "VT":
            self.vt_table.vertices.append(VT(*args))
        elif directive == "IDX":
            self.vt_table.idxes.append(args[0])
        elif directive == "IDX10":
            # idx error etc
            self.vt_table.idxes.extend(args)
        elif directive == "TRIS":
            start_idx = args[0]
            count = args[1]
            # Only when we have an animation stack and the top doesn't have an associated
            # intermediate_datablock do we do the pairing
            should_apply_animation = (
                self._current_animation and not self._current_intermediate_datablock
            )
            try:
                parent = self._anim_intermediate_stack[-2].inter_datablock
            except IndexError:
                parent = self.root_intermediate_datablock

            inter_datablock = IntermediateDatablock(
                datablock_info=DatablockInfo(
                    datablock_type="MESH",
                    name=name_hint or "next_obj_name",
                    # How do we keep track of this
                    parent_info=ParentInfo(parent.datablock_info.name),
                    collection=self.root_collection,
                ),
                start_idx=start_idx,
                count=count,
                animations_to_apply=[self._current_animation]
                if should_apply_animation
                else [],
            )
            self._blocks.append(inter_datablock)

            if should_apply_animation:
                # This is triggered if this is the 1st TRIS block after the animations
                self._current_intermediate_datablock = inter_datablock

        elif directive == "ANIM_begin":
            # breakpoint()
            self._anim_count.append(0)
        elif directive == "ANIM_end":
            # breakpoint()
            for i in range(self._anim_count.pop()):
                self._anim_intermediate_stack.pop()
        elif directive == "ANIM_trans_begin":
            # breakpoint()
            dataref_path = args[0]

            make_empty_as_needed()
            self._anim_intermediate_stack.append(
                _AnimBoneStackEntry(IntermediateAnimation(), None)
            )
            self._current_animation.xp_dataref = IntermediateDataref(
                anim_type=ANIM_TYPE_TRANSFORM,
                loop=0,
                path=dataref_path,
                show_hide_v1=0,
                show_hide_v2=0,
                values=[],
            )
            # Move this to the right place self._current_intermediate_datablock.animations_to_apply.append(self._current_animation)
            # breakpoint()
            self._anim_count[-1] += 1
        elif directive == "ANIM_trans_key":
            value = args[0]
            location = args[1]
            self._current_animation.locations.append(location)
            self._current_dataref.values.append(value)
        elif directive == "ANIM_trans_end":
            pass
        elif directive in {"ANIM_hide", "ANIM_show"}:
            v1, v2 = args[:2]
            dataref_path = args[2]
            self._current_dataref.anim_type = directive.split("_")[1]
            self._current_dataref.path = dataref_path
            self._current_dataref.show_hide_v1 = v1
            self._current_dataref.show_hide_v2 = v2
        elif directive == "ANIM_rotate_begin":
            axis = args[0]
            dataref_path = args[1]
            self._last_axis = axis
            self._current_animation.xp_dataref.append(
                IntermediateDataref(
                    anim_type=ANIM_TYPE_TRANSFORM,
                    loop=0,
                    path=dataref_path,
                    show_hide_v1=0,
                    show_hide_v2=0,
                    values=[],
                )
            )
            self._anim_count[-1] += 1
        elif directive == "ANIM_rotate_key":
            value = args[0]
            degrees = args[1]
            self._current_animation.rotations[self._last_axis.freeze()].append(degrees)
            self._current_dataref.values.append(value)
        elif directive == "ANIM_rotate_end":
            self._last_axis = None
        elif directive == "ANIM_keyframe_loop":
            loop = args[0]
            self._current_dataref.loop = loop
        else:
            # print("SKIPPING directive", directive)
            pass

    def finalize_intermediate_blocks(self) -> Set[str]:
        """The last step after parsing, converting
        data to intermediate structures, clean up and error checking.

        Returns a set with FINISHED or CANCELLED, matching the returns of bpy
        operators
        """
        # Since we're using root collections mode, our ROOT empty datablock isn't made
        # and we pretend its a collection.
        for intermediate_block in self._blocks[1:]:
            if intermediate_block.datablock_info.datablock_type == "EMPTY":
                ob = test_creation_helpers.create_datablock_empty(
                    intermediate_block.datablock_info
                )
            elif intermediate_block.datablock_info.datablock_type == "MESH":
                ob = intermediate_block.build_mesh(self.vt_table)
            for animation in intermediate_block.animations_to_apply:
                animation.apply_animation(ob)
        bpy.context.scene.frame_current = 1
        return {"FINISHED"}

    @property
    def _current_animation(self) -> IntermediateAnimation:
        try:
            return self._anim_intermediate_stack[-1].animation
        except IndexError:  # stack of none
            return None

    @_current_animation.setter
    def _current_animation(self, value: IntermediateAnimation) -> None:
        self._anim_intermediate_stack[-1].animation = value

    @property
    def _current_intermediate_datablock(self) -> Optional[IntermediateDatablock]:
        return self._anim_intermediate_stack[-1].inter_datablock

    @_current_intermediate_datablock.setter
    def _current_intermediate_datablock(self, value: IntermediateDatablock) -> None:
        self._anim_intermediate_stack[-1].inter_datablock = value

    @property
    def _current_dataref(self) -> IntermediateDataref:
        """The currenet dataref of the current animation"""
        return self._current_animation.xp_dataref

    @_current_dataref.setter
    def _current_dataref(self, value: IntermediateDataref):
        self._current_animation.xp_dataref = value

    def _next_object_name(self) -> str:
        return f"Mesh.{sum(1 for block in self.blocks if block.datablock_info.datablock_type == 'MESH'):03}"

    def _next_empty_name(self) -> str:
        return f"Mesh.{sum(1 for block in self.blocks if block.datablock_info.datablock_type == 'EMPTY'):03}"
