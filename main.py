import tomllib
import subprocess
from pathlib import Path
from typing import List, Set
from string import Template

WORK_DIR = "./"


def main():
    ed = EpicsDistrib("cfg/config.toml")
    ed.do_build()


class Dependency:
    def __init__(self, name: str, version: str, url: str, type_dep: str, env: str):
        self.name = name
        self.version = version
        self.url = url
        self.deps: List["Dependency"] = []
        self.type_dep = type_dep
        self.to_build = True if self.name == "base" else False
        self.path: Path = Path("")
        self.env = env

    def add_deps(self, dep: "Dependency"):
        self.deps.append(dep)


class DependecyGraph:
    def __init__(self):
        pass


class EpicsDistrib:
    def __init__(self, filepath: Path, workdir: Path = "workdir"):
        self.module_db: List["Dependency"] = []
        self.deps: Set[str] = set()
        self.workdir: Path = Path(workdir)
        self.load_deps("cfg/dependencies.toml")
        self.load_conf(filepath)
        # self.area_detector = False

    def load_deps(self, filepath: Path):
        file_deps = open(filepath, "rb")
        self.dict_deps = tomllib.load(file_deps)
        for key, values in self.dict_deps.items():
            module = Dependency(
                name=key,
                version=values["stable"],
                url=values["url"],
                type_dep=values["type"],
                env=values["env"],
            )
            for dep in values["depends"]:
                module.add_deps(dep)
            self.module_db.append(module)

    def load_conf(self, filepath: Path):
        file_conf = open(filepath, "rb")
        self.dict_conf = tomllib.load(file_conf)
        self.check_deps()

    def set_workdir(self, workdir: Path):
        self.workdir = workdir

    def check_deps(self):
        for key in self.dict_conf["support"]:
            for mod in self.module_db:
                if key == mod.name:
                    mod.to_build = True
                    if key == "areaDetector":
                        self.area_detector = True

        for key in self.dict_conf["extension"]:
            for mod in self.module_db:
                if key == mod.name:
                    mod.to_build = True

    def create_workdir(self):
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "support").mkdir(parents=True, exist_ok=True)
        (self.workdir / "extensions").mkdir(parents=True, exist_ok=True)
        for mod in self.module_db:
            if mod.type_dep == "base":
                mod.path = self.workdir / "base"
            else:
                mod.path = self.workdir / Path(mod.type_dep) / mod.name

    def download_module(self):
        for mod in self.module_db:
            if mod.to_build:
                self.clone(mod)
                for dep in mod.deps:
                    self.deps.add(dep)

    def do_build(self):
        self.create_workdir()
        self.download_module()
        self.create_template()
        self.deploy_template()
        self.create_makefile()
        if self.area_detector == True:
            self.create_ad_files()

    def clone(self, mod: Dependency):
        subprocess.run(
            [
                "git",
                "clone",
                "--recursive",
                mod.url,
                mod.path,
                "-b",
                mod.version,
            ],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout

    def create_template(self):
        self.release_template = Template(
            Path("template/RELEASE.local.template").read_text()
        ).substitute(EPICS_ROOT_PATH=self.workdir.absolute())

        for mod in self.module_db:
            if mod.to_build:
                if mod.type_dep == "extension":
                    stri = "{0}={1}/{2}\n".format(mod.env, "$(EXTENSIONS)", mod.name)
                    self.release_template = self.release_template + stri
                elif mod.type_dep == "support":
                    stri = "{0}={1}/{2}\n".format(mod.env, "$(SUPPORT)", mod.name)
                    self.release_template = self.release_template + stri
                else:
                    pass

        shared_str = "YES" if self.dict_conf["install"]["static"] == False else "NO"
        static_str = "YES" if self.dict_conf["install"]["static"] == True else "NO"
        target_folder = (
            "" if self.dict_conf["install"]["in-source"] == True else "INSTALL".format
        )

        self.config_template = Template(
            Path("template/CONFIG_SITE.local.template").read_text()
        ).substitute(shared=shared_str, static=static_str)

    def create_makefile(self):
        make_str = ""
        for mod in self.module_db:
            if mod.to_build and mod.type_dep == "support" and mod.name != "base":
                make_str = make_str + "{}: ".format(mod.name)
                for dep in mod.deps:
                    if dep != "base":
                        make_str = make_str + "{} ".format(dep)
                make_str = make_str + "\n"
        self.makefile_template = Template(
            Path("template/Makefile.template").read_text()
        ).substitute(order=make_str)
        self.save_template(self.workdir / "support/Makefile", self.makefile_template)

    def create_ad_files(self):
        ad_template = Template(
            Path("template/areaDetector/RELEASE_LIBS.local.template").read_text()
        ).substitute(EPICS_ROOT_PATH=self.workdir.absolute())
        self.save_template(
            self.workdir / "support/areaDetector/configure/RELEASE_LIBS.local",
            ad_template,
        )

        ad_template = Template(
            Path("template/areaDetector/RELEASE_PRODS.local.template").read_text()
        ).substitute(EPICS_ROOT_PATH=self.workdir.absolute())
        self.save_template(
            self.workdir / "support/areaDetector/configure/RELEASE_PRODS.local",
            ad_template,
        )

        ad_template = Template(
            Path("template/areaDetector/CONFIG_SITE.local.template").read_text()
        ).substitute(EPICS_ROOT_PATH=self.workdir.absolute())
        self.save_template(
            self.workdir / "support/areaDetector/configure/CONFIG_SITE.local",
            ad_template,
        )

    def save_template(self, save_filepath: Path, template: str):
        release_file = open(save_filepath, "w")
        release_file.write(template)
        release_file.close()

    def deploy_template(self):
        self.save_template(
            self.workdir / "support/RELEASE.local", self.release_template
        )
        self.save_template(
            self.workdir / "extensions/RELEASE.local", self.release_template
        )
        self.save_template(
            self.workdir / "support/CONFIG_SITE.local", self.config_template
        )
        self.save_template(
            self.workdir / "extensions/CONFIG_SITE.local", self.config_template
        )


if __name__ == "__main__":
    main()
