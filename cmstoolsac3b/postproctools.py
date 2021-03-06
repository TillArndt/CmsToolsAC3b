
import os
import shutil
import settings
import postprocessing
import rendering
import diskio
import generators as gen

class HistoPoolClearer(postprocessing.PostProcTool):
    """Empties HistoPool"""
    can_reuse = False
    has_output_dir = False

    def run(self):
        del settings.histo_pool[:]


class UnfinishedSampleRemover(postprocessing.PostProcTool):
    """Removes unfinished samples from settings.samples"""
    can_reuse = False
    has_output_dir = False

    class UnfinishedSampleStop(Exception): pass

    def __init__(self, stop_on_unfinished = False):
        super(UnfinishedSampleRemover, self).__init__()
        self.stop_on_unfinished = stop_on_unfinished

    def run(self):
        finished_procs = list(
            p.name
            for p in settings.cmsRun_procs
            if p.successful()
        )
        for sample in settings.samples.iterkeys():
            if not sample in finished_procs:
                if self.stop_on_unfinished:
                    raise self.UnfinishedSampleStop()
                else:
                    self.message(
                        "WARNING: Process '"
                        + sample
                        + "' unfinished. Removing sample from list."
                    )
                    del settings.samples[sample]


#TODO: Make a new Plotter Interface with Composition: the functions should be non-class methods
class FSStackPlotter(postprocessing.PostProcTool):
    """A 'stack with data overlay' plotter. To be subclassed."""

    class NoFilterDictError(Exception): pass

    def __init__(self, name = None):
        super(FSStackPlotter, self).__init__(name)
        if not hasattr(self, "filter_dict"):
            self.filter_dict = None
        if not hasattr(self, "canvas_decorators"):
            self.canvas_decorators = [
                rendering.BottomPlotRatioSplitErr,
                rendering.Legend
            ]
        if not hasattr(self, "hook_loaded_histos"):
            self.hook_loaded_histos = None
        if not hasattr(self, "hook_pre_canvas_build"):
            self.hook_pre_canvas_build = None
        if not hasattr(self, "hook_post_canvas_build"):
            self.hook_post_canvas_build = None
        if not hasattr(self, "save_log_scale"):
            self.save_log_scale = False
        if not hasattr(self, "save_lin_log_scale"):
            self.save_lin_log_scale = False
        self.save_name_lambda = lambda wrp: self.plot_output_dir + wrp.name

    def configure(self):
        pass

    def set_up_stacking(self):
        if not self.filter_dict:
            raise self.NoFilterDictError(
                "filter_dict not set: subclass and overwrite configure()"
            )
        wrps = gen.fs_filter_active_sort_load(self.filter_dict)
        if self.hook_loaded_histos:
            wrps = self.hook_loaded_histos(wrps)
        wrps = gen.group(wrps)
        wrps = gen.mc_stack_n_data_sum(wrps, None, True)
        wrps = gen.pool_store_items(wrps)
        self.stream_stack = wrps

    def set_up_make_canvas(self):
        def put_ana_histo_name(grps):
            for grp in grps:
                grp.name = grp.renderers[0].analyzer+"_"+grp.name
                yield grp
        def run_build_procedure(bldr):
            for b in bldr:
                b.run_procedure()
                yield b
        bldr = gen.make_canvas_builder(self.stream_stack)
        bldr = put_ana_histo_name(bldr)
        bldr = gen.decorate(bldr, self.canvas_decorators)
        if self.hook_pre_canvas_build:
            bldr = self.hook_pre_canvas_build(bldr)
        bldr = run_build_procedure(bldr)
        if self.hook_post_canvas_build:
            bldr = self.hook_post_canvas_build(bldr)
        self.stream_canvas = gen.build_canvas(bldr)

    def set_up_save_canvas(self):
        if self.save_lin_log_scale:
            self.stream_canvas = gen.save_canvas_lin_log(
                self.stream_canvas,
                self.save_name_lambda,
            )
        else:
            if self.save_log_scale:
                self.stream_canvas = gen.switch_log_scale(self.stream_canvas)
            self.stream_canvas = gen.save(
                self.stream_canvas,
                self.save_name_lambda,
            )

    def run_sequence(self):
        count = gen.consume_n_count(self.stream_canvas)
        if count:
            level = "INFO "
        else:
            level = "WARNING "
        self.message(level+self.name+" produced "+str(count)+" canvases.")

    def run(self):
        self.configure()
        self.set_up_stacking()
        self.set_up_make_canvas()
        self.set_up_save_canvas()
        self.run_sequence()


class SimpleWebCreator(postprocessing.PostProcTool):
    """
    Browses through settings.DIR_PLOTS and generates webpages recursively for
    all directories.
    """
    has_output_dir = False

    def __init__(self, name = None, working_dir = ""):
        super(SimpleWebCreator, self).__init__(name)
        self.working_dir = working_dir
        self.target_dir = settings.web_target_dir
        self.web_lines = []
        self.subfolders = []
        self.image_names = []
        self.plain_info = []
        self.plain_tex = []
        self.image_postfix = None

    def configure(self):
        """A bit of initialization."""
        if not self.working_dir:
            self.working_dir = settings.DIR_PLOTS

        # get image format
        for pf in [".png", ".jpg", ".jpeg"]:
            if pf in settings.rootfile_postfixes:
                self.image_postfix = pf
                break
        if not self.image_postfix:
            self.message("WARNING No image formats for web available!")
            self.message("WARNING settings.rootfile_postfixes:"
                         + str(settings.rootfile_postfixes))
            return

        # collect folders and images
        for wd, dirs, files in os.walk(self.working_dir):
            self.subfolders += dirs
            for f in files:
                if f[-5:] == ".info":
                    if f[:-5] + self.image_postfix in files:
                        self.image_names.append(f[:-5])
                    else:
                        self.plain_info.append(f)
                if f[-4:] == ".tex":
                    self.plain_tex.append(f)
            break

    def go4subdirs(self):
        """Walk of subfolders and start instances. Remove empty dirs."""
        for sf in self.subfolders[:]:
            path = os.path.join(self.working_dir, sf)
            inst = self.__class__()
            inst.working_dir = path
            inst.run()
            if not os.path.exists(os.path.join(path, "index.html")):
                self.subfolders.remove(sf)

    def make_html_head(self):
        self.web_lines += [
            '<html>',
            '<head>',
            '<script type="text/javascript" language="JavaScript"><!--',
            'function ToggleDiv(d) {',
            '  if(document.getElementById(d).style.display == "none") { ',
            '    document.getElementById(d).style.display = "block";',
            '  } else { ',
            '    document.getElementById(d).style.display = "none";',
            '  }',
            '}',
            '//--></script>',
            '</head>',
            '<body>'
            '<h2>'
            'DISCLAIMER: latest-super-preliminary-nightly-build-work-in-progress-analysis-snapshot'
            '</h2>'
        ]

    def make_headline(self):
        self.web_lines += (
            '<h1> Folder: ' + self.working_dir + '</h1>',
            '<hr width="60%">',
            ""
        )

    def make_subfolder_links(self):
        self.web_lines += ('<h2>Subfolders:</h2>',)
        for sf in self.subfolders:
            self.web_lines += (
                '<p><a href="'
                + os.path.join(sf, "index.html")
                + '">'
                + sf
                + '</a></p>',
            )
        self.web_lines += ('<hr width="60%">', "")

    def make_info_file_divs(self):
        self.web_lines += ('<h2>Info files:</h2>',)
        for nfo in self.plain_info:
            wrp = diskio.read(
                os.path.join(self.working_dir, nfo)
            )
            self.web_lines += (
                '<div>',
                '<p>',
                '<b>' + nfo + '</b>',
                '<p>',
                '<pre>',
                str(wrp),
                '</pre>',
                '</div>',
                '<hr width="60%">',
            )

    def make_tex_file_divs(self):
        self.web_lines += ('<h2>Tex files:</h2>',)
        for tex in self.plain_tex:
            with open(os.path.join(self.working_dir, tex), "r") as f:
                self.web_lines += (
                    '<div>',
                    '<p>',
                    '<b>' + tex + '</b>',
                    '<p>',
                    '<pre>',
                )
                self.web_lines += f.readlines()
                self.web_lines += (
                    '</pre>',
                    '</div>',
                    '<hr width="60%">',
                )

    def make_image_divs(self):
        self.web_lines += ('<h2>Images:</h2>',)
        for img in self.image_names:
            #TODO get history from full wrapper!!
            history_lines = ""
            with open(os.path.join(self.working_dir,img + ".info")) as f:
                while f.next() != "\n": continue #skip ahead to history
                for line in f:
                    history_lines += line
            h_id = "history_" + img
            self.web_lines += (
                '<div>',
                '<p>',
                '<b>' + img + ':</b>',     # image headline
                '<a href="javascript:ToggleDiv(\'' + h_id
                + '\')">(toggle history)</a>',
                '</p>',
                '<div id="' + h_id           # history div
                + '" style="display:none;"><pre>',
                history_lines,
                '</pre></div>',
                '<img src="'                 # the image itself
                + img + self.image_postfix
                + '" />',
                '</div>',
                '<hr width="95%">'
            )

    def finalize_page(self):
        self.web_lines += ["", "</body>", "</html>", ""]

    def write_page(self):
        """Write to disk."""
        for i,l in enumerate(self.web_lines):
            self.web_lines[i] += "\n"
        with open(os.path.join(self.working_dir, "index.html"), "w") as f:
            f.writelines(self.web_lines)

    def copy_page_to_destination(self):
        """Copies .htaccess to cwd. If on top, copies everything to target."""
        if not self.target_dir:
            return
        if not self.working_dir == settings.DIR_PLOTS:
            shutil.copy2(os.path.join(self.target_dir, ".htaccess"), self.working_dir)
        else:
            self.message("INFO Copying page to " + self.target_dir)
            shutil.copy2(os.path.join(self.working_dir, "index.html"), self.target_dir)
            ign_pat = shutil.ignore_patterns("*.root", "*.pdf", "*.eps", "*.info")
            for f in self.subfolders:
                shutil.rmtree(os.path.join(self.target_dir, f), True)
                shutil.copytree(
                    os.path.join(self.working_dir, f), 
                    os.path.join(self.target_dir, f),
                    ignore=ign_pat
                )

    def run(self):
        """Run the single steps."""
        self.configure()
        if not self.image_postfix: return # WARNING message above.
        if self.image_names or self.subfolders or self.plain_info:
            self.message("INFO Building page in " + self.working_dir)
        else:
            return
        self.go4subdirs()
        self.make_html_head()
        self.make_headline()
        self.make_subfolder_links()
        self.make_info_file_divs()
        self.make_tex_file_divs()
        self.make_image_divs()
        self.finalize_page()
        self.write_page()
        self.copy_page_to_destination()

