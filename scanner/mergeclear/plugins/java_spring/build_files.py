"""
Maven / Gradle build files: modules, versions and library dependencies.
"""
import logging
import os
import re
import xml.etree.ElementTree as ET

from mergeclear.core.files import SKIP_DIRS
from .catalog import classify_library

logger = logging.getLogger(__name__)

BUILD_FILES = ("pom.xml", "build.gradle", "build.gradle.kts")
MAX_BUILD_FILE_SIZE = 1_000_000

GRADLE_CONFIGURATIONS = (
    "implementation", "api", "compileOnly", "runtimeOnly", "annotationProcessor",
    "developmentOnly", "testImplementation", "testRuntimeOnly", "testCompileOnly",
    "compile", "runtime", "testCompile", "kapt",
)


def find_module_dirs(repo_path):
    """Directories (relative, '' for root) that contain a build file."""
    modules = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d != "src"]

        if any(f in files for f in BUILD_FILES):
            rel = os.path.relpath(root, repo_path)
            modules.append("" if rel == "." else rel.replace(os.sep, "/"))

    return sorted(modules)


def parse_modules(repo_path, module_dirs):
    """{module_dir: module info} for every module."""
    modules = {}
    parents = {}

    for module_dir in module_dirs:
        path = os.path.join(repo_path, module_dir)
        info = None

        try:
            if os.path.exists(os.path.join(path, "pom.xml")):
                info = _parse_pom(os.path.join(path, "pom.xml"), parents)
            else:
                gradle = next(
                    (os.path.join(path, f) for f in ("build.gradle", "build.gradle.kts")
                     if os.path.exists(os.path.join(path, f))),
                    None
                )
                if gradle:
                    info = _parse_gradle(gradle, path)
        except Exception as e:
            logger.debug("Failed to parse build file in %s: %s", path, e)

        if info is None:
            info = {"build_tool": "unknown", "dependencies": []}

        info["path"] = module_dir
        info.setdefault("name", os.path.basename(module_dir) or os.path.basename(os.path.abspath(repo_path)))
        modules[module_dir] = info

        if info.get("artifact"):
            parents[info["artifact"]] = info

    # inherit versions from parent modules (multi-module Maven)
    for info in modules.values():
        parent = parents.get((info.get("parent") or {}).get("artifact"))
        if parent:
            for key in ("group", "version", "java_version", "spring_boot_version", "spring_cloud_version"):
                if not info.get(key) and parent.get(key):
                    info[key] = parent[key]

    return modules


def _read(path):
    if os.path.getsize(path) > MAX_BUILD_FILE_SIZE:
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# ============================
# MAVEN
# ============================

def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _child_text(el, tag):
    child = el.find(tag) if el is not None else None
    return child.text.strip() if child is not None and child.text else None


def _parse_pom(path, known_parents):
    text = _read(path)
    if text is None:
        return None

    root = _strip_ns(ET.fromstring(text))

    props = {}
    props_el = root.find("properties")
    if props_el is not None:
        for p in props_el:
            if isinstance(p.tag, str):
                props[p.tag] = (p.text or "").strip()

    parent_el = root.find("parent")
    parent = None
    if parent_el is not None:
        parent = {
            "group": _child_text(parent_el, "groupId"),
            "artifact": _child_text(parent_el, "artifactId"),
            "version": _child_text(parent_el, "version"),
        }
        inherited = known_parents.get(parent["artifact"])
        if inherited:
            props = {**inherited.get("_properties", {}), **props}

    def resolve(value):
        if not value:
            return value
        for _ in range(5):
            new = re.sub(r"\$\{([^}]+)\}", lambda m: props.get(m.group(1), m.group(0)), value)
            if new == value:
                break
            value = new
        return value

    group = _child_text(root, "groupId") or (parent or {}).get("group")
    version = resolve(_child_text(root, "version")) or (parent or {}).get("version")
    props.setdefault("project.version", version or "")
    props.setdefault("project.groupId", group or "")

    boot_version = None
    if parent and parent.get("artifact") == "spring-boot-starter-parent":
        boot_version = parent.get("version")

    cloud_version = resolve(props.get("spring-cloud.version") or props.get("spring.cloud.version"))

    dependencies = []
    deps_el = root.find("dependencies")
    for dep in (deps_el if deps_el is not None else []):
        if dep.tag != "dependency":
            continue
        dependencies.append(_dependency(
            resolve(_child_text(dep, "groupId")),
            resolve(_child_text(dep, "artifactId")),
            resolve(_child_text(dep, "version")),
            _child_text(dep, "scope") or "compile",
        ))

    for dep in root.findall("dependencyManagement/dependencies/dependency"):
        artifact = _child_text(dep, "artifactId")
        if artifact == "spring-cloud-dependencies":
            cloud_version = cloud_version or resolve(_child_text(dep, "version"))
        if artifact == "spring-boot-dependencies":
            boot_version = boot_version or resolve(_child_text(dep, "version"))

    for plugin in root.findall("build/plugins/plugin"):
        if _child_text(plugin, "artifactId") == "spring-boot-maven-plugin" and not boot_version:
            boot_version = resolve(_child_text(plugin, "version"))

    return {
        "build_tool": "maven",
        "name": _child_text(root, "artifactId"),
        "group": group,
        "artifact": _child_text(root, "artifactId"),
        "version": version,
        "packaging": _child_text(root, "packaging") or "jar",
        "description": _child_text(root, "description"),
        "parent": parent,
        "java_version": resolve(props.get("java.version") or props.get("maven.compiler.release") or props.get("maven.compiler.source")),
        "spring_boot_version": boot_version,
        "spring_cloud_version": cloud_version,
        "submodules": [m.text.strip() for m in root.findall("modules/module") if m.text],
        "dependencies": dependencies,
        "_properties": props,
    }


# ============================
# GRADLE
# ============================

def _parse_gradle(path, module_path):
    text = _read(path)
    if text is None:
        return None

    # drop comments
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)//.*$", "", text)

    dependencies = []
    configs = "|".join(GRADLE_CONFIGURATIONS)
    dep_re = re.compile(
        rf"\b({configs})\s*\(?\s*(?:platform\s*\(\s*)?['\"]([^'\"]+)['\"]"
    )

    for config, coordinate in dep_re.findall(text):
        parts = coordinate.split(":")
        if len(parts) < 2:
            continue
        scope = "test" if config.startswith("test") else (
            "runtime" if "runtime" in config.lower() else
            "provided" if config in ("compileOnly", "annotationProcessor", "kapt") else "compile"
        )
        dependencies.append(_dependency(parts[0], parts[1], parts[2] if len(parts) > 2 else None, scope))

    for config, project in re.findall(rf"\b({configs})\s*\(?\s*project\s*\(\s*['\"]:?([^'\"]+)['\"]", text):
        dependencies.append({"group": None, "artifact": project, "version": None, "scope": "compile",
                             "category": "internal-module", "technology": project})

    boot = re.search(r"org\.springframework\.boot['\"]\s*\)?\s*version\s*['\"]([^'\"]+)", text)
    group = re.search(r"(?m)^\s*group\s*=\s*['\"]([^'\"]+)", text)
    version = re.search(r"(?m)^\s*version\s*=\s*['\"]([^'\"]+)", text)
    java = (
        re.search(r"JavaLanguageVersion\.of\(\s*(\d+)", text)
        or re.search(r"sourceCompatibility\s*=\s*['\"]?(?:JavaVersion\.VERSION_)?([\d_.]+)", text)
    )
    cloud = re.search(r"springCloudVersion['\"]?\s*,?\s*=?\s*['\"]([^'\"]+)", text)

    name = os.path.basename(module_path)
    for settings in ("settings.gradle", "settings.gradle.kts"):
        settings_path = os.path.join(module_path, settings)
        if os.path.exists(settings_path):
            match = re.search(r"rootProject\.name\s*=\s*['\"]([^'\"]+)", _read(settings_path) or "")
            if match:
                name = match.group(1)

    return {
        "build_tool": "gradle",
        "name": name,
        "artifact": name,
        "group": group.group(1) if group else None,
        "version": version.group(1) if version else None,
        "java_version": java.group(1).replace("_", ".") if java else None,
        "spring_boot_version": boot.group(1) if boot else None,
        "spring_cloud_version": cloud.group(1) if cloud else None,
        "dependencies": dependencies,
    }


def _dependency(group, artifact, version, scope):
    category, technology = classify_library(group or "", artifact or "")
    return {
        "group": group,
        "artifact": artifact,
        "version": version,
        "scope": scope,
        "category": category,
        "technology": technology,
    }


def public_module(info):
    """Module info without internal keys."""
    return {k: v for k, v in info.items() if not k.startswith("_")}
