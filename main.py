import tomllib
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Set
from string import Template
import logging

WORK_DIR = "./"

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


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
        self.max_worker = 8
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
        self.workdir = Path(self.dict_conf["install"]["workdir"])
        self.check_deps()

    def set_workdir(self, workdir: Path):
        self.workdir = workdir

    def check_deps(self):
        for key in self.dict_conf["support"]:
            for mod in self.module_db:
                if key == mod.name:
                    logging.info("Module {} found".format(mod.name))
                    mod.to_build = True
                    if key == "areaDetector":
                        self.area_detector = True

        for key in self.dict_conf["extension"]:
            for mod in self.module_db:
                if key == mod.name:
                    mod.to_build = True

    def create_workdir(self):
        logging.info("Create {} folder".format(self.workdir))
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "support").mkdir(parents=True, exist_ok=True)
        (self.workdir / "extension").mkdir(parents=True, exist_ok=True)
        for mod in self.module_db:
            if mod.type_dep == "base":
                mod.path = self.workdir / "base"
            else:
                mod.path = self.workdir / Path(mod.type_dep) / mod.name

    def download_module(self):
        logging.info("Download modules with git")
        cmds = []
        for mod in self.module_db:
            if mod.to_build:
                cmds.append(self.create_git_cmd(mod))
                for dep in mod.deps:
                    self.deps.add(dep)
        results = []

        with ThreadPoolExecutor(max_workers=self.max_worker) as executor:
            futures = [executor.submit(self.run_cmd, cmd) for cmd in cmds]
            for future in as_completed(futures):
                results.append(future.result())

    def do_build(self):
        self.create_workdir()
        self.download_module()
        self.create_template()
        self.deploy_template()
        self.create_makefile()
        if self.area_detector == True:
            self.create_ad_files()

    def create_git_cmd(self, mod: Dependency):
        return [
            "git",
            "clone",
            "--recursive",
            mod.url,
            mod.path,
            "-b",
            mod.version,
        ]

    def run_cmd(self, cmd):
        logging.info("Run command {} for {}".format(cmd[0], cmd[3]))
        result = subprocess.run(cmd, capture_output=True, text=True)
        return cmd, result.returncode, result.stdout, result.stderr

    def create_template(self):
        logging.info("Create RELEASE.local")
        self.release_template = Template(
            Path("template/RELEASE.local.template").read_text()
        ).substitute(EPICS_ROOT_PATH=self.workdir.absolute())

        for mod in self.module_db:
            if mod.to_build:
                if mod.type_dep == "extension":
                    stri = "{0}={1}/{2}\n".format(mod.env, "$(EXTENSION)", mod.name)
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

        logging.info("Create CONFIG_SITE.local")
        self.config_template = Template(
            Path("template/CONFIG_SITE.local.template").read_text()
        ).substitute(shared=shared_str, static=static_str)

    def create_makefile(self):
        logging.info("Create Makefile")
        make_str = ""
        for mod in self.module_db:
            if mod.to_build and mod.type_dep == "support" and mod.name != "base":
                make_str = make_str + "{}: ".format(mod.name)
                for dep in mod.deps:
                    if dep != "base":
                        make_str = make_str + "{} ".format(dep)
                make_str = make_str + "\n"
        make_str = (
            make_str
            + "all: "
            + " ".join(
                [
                    mod.name
                    for mod in self.module_db
                    if (
                        mod.to_build
                        and mod.type_dep == "support"
                        and mod.name != "base"
                    )
                ]
            )
            + "\n"
        )
        self.makefile_template = Template(
            Path("template/Makefile.template").read_text()
        ).substitute(order=make_str)
        self.save_template(self.workdir / "support/Makefile", self.makefile_template)

    def create_ad_files(self):
        logging.info("Create specific file for AreaDetector")
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
            self.workdir / "extension/RELEASE.local", self.release_template
        )
        self.save_template(
            self.workdir / "support/CONFIG_SITE.local", self.config_template
        )
        self.save_template(
            self.workdir / "extension/CONFIG_SITE.local", self.config_template
        )


if __name__ == "__main__":
    main()
