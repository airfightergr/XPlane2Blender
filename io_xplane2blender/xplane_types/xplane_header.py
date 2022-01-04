import os
import platform
import re
from collections import OrderedDict
from pathlib import Path
from typing import List

import bpy

from io_xplane2blender.xplane_constants import EXPORT_TYPE_AIRCRAFT, EXPORT_TYPE_SCENERY

from ..xplane_constants import *
from ..xplane_helpers import (
    effective_normal_metalness,
    effective_normal_metalness_draped,
    floatToStr,
    logger,
    resolveBlenderPath,
)
from .xplane_attribute import XPlaneAttribute
from .xplane_attributes import XPlaneAttributes


class XPlaneHeader:
    """
    Writes OBJ info related to the OBJ8 header, such as POINT_COUNTS and TEXTURE.
    Also the starting point for is responsible for autodetecting and compositing textures.
    """

    def __init__(self, xplaneFile: "XPlaneFile", obj_version: int) -> None:
        self.obj_version = obj_version
        self.xplaneFile = xplaneFile

        # A list of tuples in the form of (lib path, physical path)
        # for example, if the path in the box is 'lib/g10/cars/car.obj'
        # and the file is getting exported to '/code/x-plane/Custom Scenery/Kansas City/cars/honda.obj'
        # you would have ('lib/g10/cars/car.obj','cars/honda.obj')
        self.export_path_dirs = []  # type: List[str,str]

        for export_path_directive in self.xplaneFile.options.export_path_directives:
            export_path_directive.export_path = (
                export_path_directive.export_path.lstrip()
            )
            if len(export_path_directive.export_path) == 0:
                continue

            cleaned_path = bpy.data.filepath.replace("\\", "/")
            #              everything before
            #               |         scenery directory
            #               |               |        one directory afterward
            #               |               |                   |    optional directories and path to .blend file
            #               |               |                   |        |
            #               v               v                   v        v
            regex_str = r"(.*(Custom Scenery|default_scenery)(/[^/]+/)(.*))"
            potential_match = re.match(regex_str, cleaned_path)

            if potential_match is None:
                logger.error(
                    f'Export path {export_path_directive.export_path} is not properly formed. Ensure it contains the words "Custom Scenery" or "default_scenery" followed by a directory'
                )
                return  # TODO: Returning early in an __init__!
            else:
                last_folder = os.path.dirname(potential_match.group(4)).split("/")[-1:][
                    0
                ]

                if len(last_folder) > 0:
                    last_folder += "/"  # Re-append slash

                self.export_path_dirs.append(
                    (
                        export_path_directive.export_path,
                        last_folder + xplaneFile.filename + ".obj",
                    )
                )

        self.attributes = XPlaneAttributes()

        # object attributes
        self.attributes.add(XPlaneAttribute("PARTICLE_SYSTEM", None))
        self.attributes.add(XPlaneAttribute("ATTR_layer_group", None))
        self.attributes.add(XPlaneAttribute("COCKPIT_REGION", None))
        self.attributes.add(XPlaneAttribute("DEBUG", None))
        self.attributes.add(XPlaneAttribute("GLOBAL_cockpit_lit", None))
        self.attributes.add(XPlaneAttribute("GLOBAL_tint", None))
        self.attributes.add(XPlaneAttribute("REQUIRE_WET", None))
        self.attributes.add(XPlaneAttribute("REQUIRE_DRY", None))
        self.attributes.add(XPlaneAttribute("SLOPE_LIMIT", None))
        self.attributes.add(XPlaneAttribute("slung_load_weight", None))
        self.attributes.add(XPlaneAttribute("TILTED", None))

        # shader attributes
        self.attributes.add(XPlaneAttribute("TEXTURE", None))
        self.attributes.add(XPlaneAttribute("TEXTURE_LIT", None))
        self.attributes.add(XPlaneAttribute("TEXTURE_NORMAL", None))
        self.attributes.add(
            XPlaneAttribute("NORMAL_METALNESS", None)
        )  # NORMAL_METALNESS for textures
        self.attributes.add(XPlaneAttribute("GLOBAL_no_blend", None))
        self.attributes.add(XPlaneAttribute("GLOBAL_no_shadow", None))
        self.attributes.add(XPlaneAttribute("GLOBAL_shadow_blend", None))
        self.attributes.add(XPlaneAttribute("GLOBAL_specular", None))
        self.attributes.add(XPlaneAttribute("BLEND_GLASS", None))

        # draped shader attributes
        self.attributes.add(XPlaneAttribute("TEXTURE_DRAPED", None))
        self.attributes.add(XPlaneAttribute("TEXTURE_DRAPED_NORMAL", None))

        # This is a hack to get around duplicate keynames!
        # There is no NORMAL_METALNESS_draped_hack,
        # self.write will check later for draped_hack and remove it
        #
        # If later on we have more duplicate keynames we'll figure something
        # else out. -Ted, 8/9/2018
        self.attributes.add(
            XPlaneAttribute("NORMAL_METALNESS_draped_hack", None)
        )  # normal_metalness for draped textures
        self.attributes.add(XPlaneAttribute("BUMP_LEVEL", None))
        self.attributes.add(XPlaneAttribute("NO_BLEND", None))
        self.attributes.add(XPlaneAttribute("SPECULAR", None))

        # draped general attributes
        self.attributes.add(XPlaneAttribute("ATTR_layer_group_draped", None))
        self.attributes.add(XPlaneAttribute("ATTR_LOD_draped", None))

        self.attributes.add(XPlaneAttribute("EXPORT", None))

        # previously labeled object attributes, it must be the last thing
        self.attributes.add(XPlaneAttribute("POINT_COUNTS", None))

    def _init(self):
        """
        This must be called after all other collection is done. This is needed
        to decide if GLOBALs should replace certain ATTR_s

        The reason is we can only tell if certain directives should be written
        after everything is collected (like GLOBALs)
        """
        isAircraft = self.xplaneFile.options.export_type == EXPORT_TYPE_AIRCRAFT
        isCockpit = self.xplaneFile.options.export_type == EXPORT_TYPE_COCKPIT
        isInstance = (
            self.xplaneFile.options.export_type == EXPORT_TYPE_INSTANCED_SCENERY
        )
        isScenery = self.xplaneFile.options.export_type == EXPORT_TYPE_SCENERY

        canHaveDraped = self.xplaneFile.options.export_type not in [
            EXPORT_TYPE_AIRCRAFT,
            EXPORT_TYPE_COCKPIT,
        ]

        # layer groups
        if self.xplaneFile.options.layer_group != LAYER_GROUP_NONE:
            self.attributes["ATTR_layer_group"].setValue(
                (
                    self.xplaneFile.options.layer_group,
                    self.xplaneFile.options.layer_group_offset,
                )
            )

        # draped layer groups
        if (
            canHaveDraped
            and self.xplaneFile.options.layer_group_draped != LAYER_GROUP_NONE
        ):
            self.attributes["ATTR_layer_group_draped"].setValue(
                (
                    self.xplaneFile.options.layer_group_draped,
                    self.xplaneFile.options.layer_group_draped_offset,
                )
            )

        # set slung load
        if self.xplaneFile.options.slungLoadWeight > 0:
            self.attributes["slung_load_weight"].setValue(
                self.xplaneFile.options.slungLoadWeight
            )

        # set Texture
        blenddir = os.path.dirname(bpy.context.blend_data.filepath)

        # normalize the exporpath
        if os.path.isabs(self.xplaneFile.filename):
            exportdir = os.path.dirname(os.path.normpath(self.xplaneFile.filename))
        else:
            exportdir = os.path.dirname(
                os.path.abspath(
                    os.path.normpath(os.path.join(blenddir, self.xplaneFile.filename))
                )
            )

        if self.xplaneFile.options.autodetectTextures:
            # 2.8 doesn't work with Texture Slots Anymore. self._autodetectTextures()
            pass

        # standard textures
        if self.xplaneFile.options.texture != "":
            try:
                self.attributes["TEXTURE"].setValue(
                    self.get_path_relative_to_dir(
                        self.xplaneFile.options.texture, exportdir
                    )
                )
            except (OSError, ValueError):
                pass

        if self.xplaneFile.options.texture_lit != "":
            try:
                self.attributes["TEXTURE_LIT"].setValue(
                    self.get_path_relative_to_dir(
                        self.xplaneFile.options.texture_lit, exportdir
                    )
                )
            except (OSError, ValueError):
                pass

        if self.xplaneFile.options.texture_normal != "":
            try:
                self.attributes["TEXTURE_NORMAL"].setValue(
                    self.get_path_relative_to_dir(
                        self.xplaneFile.options.texture_normal, exportdir
                    )
                )
            except (OSError, ValueError):
                pass

        xplane_version = int(bpy.context.scene.xplane.version)
        if xplane_version >= 1100:
            texture_normal = self.attributes["TEXTURE_NORMAL"].getValue()
            normal_metalness = effective_normal_metalness(self.xplaneFile)
            if texture_normal:
                self.attributes["NORMAL_METALNESS"].setValue(normal_metalness)
            elif not texture_normal and normal_metalness:
                logger.warn(
                    f"{self.xplaneFile.filename}: No Normal Texture found, ignoring use of Normal Metalness"
                )

        if xplane_version >= 1100:

            if self.xplaneFile.options.export_type in {
                EXPORT_TYPE_AIRCRAFT,
                EXPORT_TYPE_COCKPIT,
            }:
                self.attributes["BLEND_GLASS"].setValue(
                    self.xplaneFile.options.blend_glass
                )
            elif (
                self.xplaneFile.options.export_type
                in {
                    EXPORT_TYPE_INSTANCED_SCENERY,
                    EXPORT_TYPE_SCENERY,
                }
                and self.xplaneFile.options.blend_glass
            ):
                logger.error(
                    f"{self.xplaneFile.filename} can't use 'Blend Glass'. 'Blend Glass' is only for Aircraft and Cockpits"
                )

        if canHaveDraped:
            # draped textures
            if self.xplaneFile.options.texture_draped != "":
                try:
                    self.attributes["TEXTURE_DRAPED"].setValue(
                        self.get_path_relative_to_dir(
                            self.xplaneFile.options.texture_draped, exportdir
                        )
                    )
                except (OSError, ValueError):
                    pass

            if self.xplaneFile.options.texture_draped_normal != "":
                # Special "1.0" required by X-Plane
                # "That's the scaling factor for the normal map available ONLY for the draped info. Without that , it can't find the texture.
                # That makes a non-fatal error in x-plane. Without the normal map, the metalness directive is ignored" -Ben Supnik, 07/06/17 8:35pm
                try:
                    self.attributes["TEXTURE_DRAPED_NORMAL"].setValue(
                        "1.0 "
                        + self.get_path_relative_to_dir(
                            self.xplaneFile.options.texture_draped_normal,
                            exportdir,
                        )
                    )
                except (OSError, ValueError):
                    pass

            if self.xplaneFile.referenceMaterials[1]:
                mat = self.xplaneFile.referenceMaterials[1]
                if xplane_version >= 1100:
                    texture_draped_nml = self.attributes[
                        "TEXTURE_DRAPED_NORMAL"
                    ].getValue()
                    normal_metalness_draped = effective_normal_metalness_draped(
                        self.xplaneFile
                    )
                    if texture_draped_nml:
                        self.attributes["NORMAL_METALNESS_draped_hack"].setValue(
                            normal_metalness_draped
                        )
                    elif not texture_draped_nml and normal_metalness_draped:
                        logger.warn(
                            f"{self.xplaneFile.filename}: No Draped Normal Texture found, ignoring use of Normal Metalness"
                        )

                # draped bump level
                if mat.options.bump_level != 1.0:
                    self.attributes["BUMP_LEVEL"].setValue(mat.bump_level)

                # draped no blend
                self.attributes["NO_BLEND"].setValue(
                    mat.attributes["ATTR_no_blend"].getValue()
                )
                # prevent of writing again in material
                mat.attributes["ATTR_no_blend"].setValue(None)

                # draped specular
                if xplane_version >= 1100 and effective_normal_metalness_draped(
                    self.xplaneFile
                ):
                    # draped specular
                    self.attributes["SPECULAR"].setValue(1.0)
                else:
                    # draped specular
                    self.attributes["SPECULAR"].setValue(
                        mat.attributes["ATTR_shiny_rat"].getValue()
                    )

                # prevent of writing again in material
                mat.attributes["ATTR_shiny_rat"].setValue(None)
            # draped LOD
            if self.xplaneFile.options.lod_draped != 0.0:
                self.attributes["ATTR_LOD_draped"].setValue(
                    self.xplaneFile.options.lod_draped
                )

        # set cockpit regions
        if isAircraft or isCockpit:
            num_regions = int(self.xplaneFile.options.cockpit_regions)

            if num_regions > 0:
                self.attributes["COCKPIT_REGION"].removeValues()
                for i in range(0, num_regions):
                    cockpit_region = self.xplaneFile.options.cockpit_region[i]
                    self.attributes["COCKPIT_REGION"].addValue(
                        (
                            cockpit_region.left,
                            cockpit_region.top,  # bad name alert! Should have been "bottom"
                            cockpit_region.left + (2 ** cockpit_region.width),
                            cockpit_region.top + (2 ** cockpit_region.height),
                        )
                    )

        if xplane_version >= 1130:
            try:
                pss = self.get_path_relative_to_dir(
                    self.xplaneFile.options.particle_system_file, exportdir
                )
            except (OSError, ValueError):
                pss = None

            if self.xplaneFile.options.particle_system_file and pss:
                if os.path.isabs(self.xplaneFile.filename):
                    exportdir = os.path.dirname(
                        os.path.normpath(self.xplaneFile.filename)
                    )
                else:
                    exportdir = os.path.dirname(
                        os.path.abspath(
                            os.path.normpath(
                                os.path.join(blenddir, self.xplaneFile.filename)
                            )
                        )
                    )

                objs = self.xplaneFile.get_xplane_objects()

                if not list(
                    filter(
                        lambda obj: obj.type == "EMPTY"
                        and obj.blenderObject.xplane.special_empty_props.special_type
                        == EMPTY_USAGE_EMITTER_PARTICLE
                        or obj.blenderObject.xplane.special_empty_props.special_type
                        == EMPTY_USAGE_EMITTER_SOUND,
                        objs,
                    )
                ):
                    logger.warn(
                        "Particle System File {} is given, but no emitter objects are used".format(
                            pss
                        )
                    )

                if not pss.endswith(".pss"):
                    logger.error(
                        "Particle System File {} must be a .pss file".format(pss)
                    )

                self.attributes["PARTICLE_SYSTEM"].setValue(pss)

        # get point counts
        tris = len(self.xplaneFile.mesh.vertices)
        lines = 0
        lights = len(self.xplaneFile.lights.items)
        indices = len(self.xplaneFile.mesh.indices)

        self.attributes["POINT_COUNTS"].setValue((tris, lines, lights, indices))

        write_user_specular_values = True

        if xplane_version >= 1100 and self.xplaneFile.referenceMaterials[0]:
            mat = self.xplaneFile.referenceMaterials[0]
            if effective_normal_metalness(self.xplaneFile):
                self.attributes["GLOBAL_specular"].setValue(1.0)
                self.xplaneFile.commands.written[
                    "ATTR_shiny_rat"
                ] = 1.0  # Here we are fooling ourselves
                write_user_specular_values = False  # It will be skipped from now on

        # v1000
        if xplane_version >= 1000:
            if (
                self.xplaneFile.options.export_type == EXPORT_TYPE_INSTANCED_SCENERY
                and self.xplaneFile.referenceMaterials[0]
            ):
                mat = self.xplaneFile.referenceMaterials[0]

                # no blend
                attr = mat.attributes["ATTR_no_blend"]
                if attr.getValue():
                    self.attributes["GLOBAL_no_blend"].setValue(attr.getValue())
                    self.xplaneFile.commands.written["ATTR_no_blend"] = attr.getValue()

                # shadow blend
                attr = mat.attributes["ATTR_shadow_blend"]
                if attr.getValue():
                    self.attributes["GLOBAL_shadow_blend"].setValue(attr.getValue())
                    self.xplaneFile.commands.written[
                        "ATTR_shadow_blend"
                    ] = attr.getValue()

                # specular
                attr = mat.attributes["ATTR_shiny_rat"]
                if write_user_specular_values and attr.getValue():
                    self.attributes["GLOBAL_specular"].setValue(attr.getValue())
                    self.xplaneFile.commands.written["ATTR_shiny_rat"] = attr.getValue()

                # tint
                if self.xplaneFile.options.tint:
                    self.attributes["GLOBAL_tint"].setValue(
                        (
                            self.xplaneFile.options.tint_albedo,
                            self.xplaneFile.options.tint_emissive,
                        )
                    )

            if not isCockpit:
                # tilted
                if self.xplaneFile.options.tilted == True:
                    self.attributes["TILTED"].setValue(True)

                # slope_limit
                if self.xplaneFile.options.slope_limit == True:
                    self.attributes["SLOPE_LIMIT"].setValue(
                        (
                            self.xplaneFile.options.slope_limit_min_pitch,
                            self.xplaneFile.options.slope_limit_max_pitch,
                            self.xplaneFile.options.slope_limit_min_roll,
                            self.xplaneFile.options.slope_limit_max_roll,
                        )
                    )

                # require surface
                if self.xplaneFile.options.require_surface == REQUIRE_SURFACE_WET:
                    self.attributes["REQUIRE_WET"].setValue(True)
                elif self.xplaneFile.options.require_surface == REQUIRE_SURFACE_DRY:
                    self.attributes["REQUIRE_DRY"].setValue(True)

        # v1010
        if xplane_version >= 1010:
            if (
                isInstance or isScenery
            ):  # An exceptional case where a GLOBAL_ is allowed in Scenery type
                mats = self.xplaneFile.getMaterials()
                if mats and all(not mat.options.shadow_local for mat in mats):
                    # No mix and match! Great!
                    self.attributes["GLOBAL_no_shadow"].setValue(True)

                if self.attributes["GLOBAL_no_shadow"].getValue():
                    for mat in mats:
                        # Erase the collected material's value, ensuring it won't be written
                        # "All ATTR_shadow is false" guaranteed by GLOBAL_no_shadow
                        mat.attributes["ATTR_no_shadow"].setValue(None)

            # cockpit_lit
            if isAircraft or isCockpit:
                if self.xplaneFile.options.cockpit_lit or xplane_version >= 1100:
                    self.attributes["GLOBAL_cockpit_lit"].setValue(True)

        if len(self.export_path_dirs):
            self.attributes["EXPORT"].value = [
                path_dir[0] + " " + path_dir[1] for path_dir in self.export_path_dirs
            ]

        for attr in self.xplaneFile.options.customAttributes:
            self.attributes.add(XPlaneAttribute(attr.name, attr.value))

    def get_path_relative_to_dir(self, res_path: str, export_dir: str) -> str:
        """
        Returns the resource path relative to the exported OBJ

        res_path   - The relative or absolute resource path (such as .png, .dds, or .pss)
                  as found in an RNA field
        export_dir - Absolute path to directory of OBJ export

        Raises ValueError or OSError for invalid paths or use of `//` not at the start of the respath
        """
        res_path = res_path.strip()
        if res_path.startswith("./") or res_path.startswith(".\\"):
            res_path = res_path.replace("./", "//").replace(".\\", "//").strip()

        # 9. '//', or none means "none", empty is not written -> str.replace
        if res_path == "":
            raise ValueError
        elif res_path == "//" or res_path == "none":
            return "none"
        # 2. '//' is the .blend folder or CWD if not saved, -> bpy.path.abspath if bpy.data.filename else cwd
        elif res_path.startswith("//") and bpy.data.filepath:
            res_path = Path(bpy.path.abspath(res_path))
        elif res_path.startswith("//") and not bpy.data.filepath:
            res_path = Path(".") / Path(res_path[2:])
        # 7. Invalid paths are a validation error -> Path.resolve throws OSError
        elif "//" in res_path and not res_path.startswith("//"):
            logger.error(f"'//' is used not at the start of the path '{res_path}'")
            raise ValueError
        elif not Path(res_path).suffix:
            logger.error(
                f"Resource path '{res_path}' must be a supported file type, has no extension"
            )
            raise ValueError
        elif Path(res_path).suffix.lower() not in {".png", ".dds", ".pss"}:
            logger.error(
                f"Resource path '{res_path}' must be a supported file type, is {Path(res_path).suffix}"
            )
            raise ValueError
        else:
            res_path = Path(res_path)

        old_cwd = os.getcwd()
        if bpy.data.filepath:
            # This makes '.' the .blend file directory
            os.chdir(Path(bpy.data.filepath).parent)
        else:
            os.chdir(Path(export_dir))

        try:
            # 1. '.' is CWD -> Path.resolve
            # 3. All paths are given '/' sperators -> Path.resolve
            # 4. '..'s are resolved, '.' is a no-op -> Path.resolve
            # 5. All paths must be relative to the OBJ -> Path.relative_to(does order of args matter)?
            # 7. Invalid paths are a validation error -> Path.resolve throws OSError
            # 8. Paths are minimal, "./path/tex.png" is "path/tex.png" -> Path.resolve
            # 10. Absolute paths are okay as long as we can make a relative path os.path.relpath
            rel_path = os.path.relpath(res_path.resolve(), export_dir).replace(
                "\\", "/"
            )
        except OSError:
            logger.error(f"Path '{res_path}' is invalid")
            os.chdir(old_cwd)
            raise
        except ValueError:
            logger.error(
                f"Cannot make relative path across disk drives for path '{res_path}'"
            )
            # 6. If not possible (different drive letter), validation error Path.relative_to ValueError
            # 7. Invalid paths are a validation error -> Path.resolve throws OSError
            os.chdir(old_cwd)
            raise
        else:
            os.chdir(old_cwd)
            return rel_path

    # Method: _getCanonicalTexturePath
    # Returns normalized (canonical) path to texture
    #
    # Parameters:
    #   string texpath - the relative or absolute texture path as chosen by the user
    #
    # Returns:
    #   string - the absolute/normalized path to the texture
    def _getCanonicalTexturePath(self, texpath) -> str:
        blenddir = os.path.dirname(bpy.context.blend_data.filepath)

        if texpath[0:2] == "//":
            texpath = texpath[2:]

        if os.path.isabs(texpath):
            texpath = os.path.abspath(os.path.normpath(texpath))
        else:
            texpath = os.path.abspath(os.path.normpath(os.path.join(blenddir, texpath)))

        return texpath

    def write(self) -> str:
        """
        Writes the collected Blender and XPlane2Blender data
        as content for the OBJ
        """
        self._init()
        system = platform.system()

        # line ending types (I = UNIX/DOS, A = MacOS)
        if "Mac OS" in system:
            o = "A\n"
        else:
            o = "I\n"

        # obj version number
        if self.obj_version >= 8:
            o += "800\n"

        o += "OBJ\n\n"

        self.attributes.move_to_end("POINT_COUNTS")

        # attributes
        for attr_name, attr in self.attributes.items():
            if attr_name == "NORMAL_METALNESS_draped_hack":
                # Hack: See note in __init__
                attr.name = "NORMAL_METALNESS"

            values = attr.value
            if values[0] != None:
                if len(values) > 1:
                    for vi in range(0, len(values)):
                        o += "%s\t%s\n" % (attr.name, attr.getValueAsString(vi))

                else:
                    # This is a double fix. Boolean values with True get written (sans the word true), False does not,
                    # and strings that start with True or False don't get treated as as booleans
                    is_bool = len(values) == 1 and isinstance(values[0], bool)
                    if is_bool and values[0] == True:
                        o += "%s\n" % (attr.name)
                    elif (
                        not is_bool
                    ):  # True case already taken care of, don't care about False case - implicitly skipped
                        o += "%s\t%s\n" % (attr.name, attr.getValueAsString())

        return o
