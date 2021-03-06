
import json
import md5
import subprocess

import bug_reducer_utils

import list_reducer
from list_reducer import TESTRESULT_KEEPPREFIX
from list_reducer import TESTRESULT_KEEPSUFFIX
from list_reducer import TESTRESULT_NOFAILURE


class ReduceMiscompilingPasses(list_reducer.ListReducer):

    def __init__(self, l, invoker):
        list_reducer.ListReducer.__init__(self, l)
        self.invoker = invoker

    def run_test(self, prefix, suffix):
        # First, run the program with just the Suffix passes.  If it is still
        # broken with JUST the kept passes, discard the prefix passes.
        suffix_joined = ' '.join(suffix)
        suffix_hash = md5.md5(suffix_joined).hexdigest()
        print("Checking to see if '%s' compiles correctly" % suffix_joined)

        result = self.invoker.invoke_with_passlist(
            suffix,
            self.invoker.get_suffixed_filename(suffix_hash))

        # Found a miscompile! Keep the suffix
        if result != 0:
            print("Suffix maintains the predicate. Returning suffix")
            return (TESTRESULT_KEEPSUFFIX, prefix, suffix)

        if len(prefix) == 0:
            print("Suffix passes and no prefix, returning nofailure")
            return (TESTRESULT_NOFAILURE, prefix, suffix)

        # Next see if the program is broken if we run the "prefix" passes
        # first, then separately run the "kept" passes.
        prefix_joined = ' '.join(prefix)
        prefix_hash = md5.md5(prefix_joined).hexdigest()
        print("Checking to see if '%s' compiles correctly" % prefix_joined)

        # If it is not broken with the kept passes, it's possible that the
        # prefix passes must be run before the kept passes to break it.  If
        # the program WORKS after the prefix passes, but then fails if running
        # the prefix AND kept passes, we can update our bitcode file to
        # include the result of the prefix passes, then discard the prefix
        # passes.
        prefix_path = self.invoker.get_suffixed_filename(prefix_hash)
        result = self.invoker.invoke_with_passlist(
            prefix,
            prefix_path)
        if result != 0:
            print("Prefix maintains the predicate by itself. Returning keep "
                  "prefix")
            return (TESTRESULT_KEEPPREFIX, prefix, suffix)

        # Ok, so now we know that the prefix passes work, first check if we
        # actually have any suffix passes. If we don't, just return.
        if len(suffix) == 0:
            print("No suffix, and prefix passes, returning no failure")
            return (TESTRESULT_NOFAILURE, prefix, suffix)

        # Otherwise, treat the prefix as our new baseline and see if suffix on
        # the new baseline finds the crash.
        original_input_file = self.invoker.input_file
        self.invoker.input_file = prefix_path
        print("Checking to see if '%s' compiles correctly after the '%s' "
              "passes" % (suffix_joined, prefix_joined))
        result = self.invoker.invoke_with_passlist(
            suffix,
            self.invoker.get_suffixed_filename(suffix_hash))

        # If we failed at this point, then the prefix is our new
        # baseline. Return keep suffix.
        if result != 0:
            print("Suffix failed. Keeping prefix as new baseline")
            return (TESTRESULT_KEEPSUFFIX, prefix, suffix)

        # Otherwise, we must not be running the bad pass anymore. Restore the
        # original input_file and return NoFailure.
        self.invoker.input_file = original_input_file
        return (TESTRESULT_NOFAILURE, prefix, suffix)


def pass_bug_reducer(args):
    """Given a path to a sib file with canonical sil, attempt to find a perturbed
list of passes that the perf pipeline"""
    tools = bug_reducer_utils.SwiftTools(args.swift_build_dir)

    passes = []
    early_module_passes = []
    if args.pass_list is None:
        json_data = json.loads(subprocess.check_output(
            [tools.sil_passpipeline_dumper, '-Performance']))
        passes = sum((p[2:] for p in json_data if p[0] != 'EarlyModulePasses'), [])
        passes = ['-' + x[1] for x in passes]
        # We assume we have an early module passes that runs until fix point and
        # that is strictly not what is causing the issue.
        #
        # Everything else runs one iteration.
        early_module_passes = [p[2:] for p in json_data
                               if p[0] == 'EarlyModulePasses'][0]
        early_module_passes = ['-' + x[1] for x in early_module_passes]
    else:
        passes = ['-' + x for x in args.pass_list]

    extra_args = []
    if args.extra_args is not None:
        extra_args = args.extra_args
    sil_opt_invoker = bug_reducer_utils.SILOptInvoker(args, tools,
                                                      early_module_passes,
                                                      extra_args)

    # Make sure that the base case /does/ crash.
    filename = sil_opt_invoker.get_suffixed_filename('base_case')
    result = sil_opt_invoker.invoke_with_passlist(passes, filename)
    # If we succeed, there is no further work to do.
    if result == 0:
        print("Success with PassList: %s" % (' '.join(passes)))
        return

    # Otherwise, reduce the list of pases that cause the optimzier to crash.
    r = ReduceMiscompilingPasses(passes, sil_opt_invoker)
    if not r.reduce_list():
        print("Failed to find miscompiling pass list!")
    cmdline = sil_opt_invoker.cmdline_with_passlist(r.target_list)
    print("*** Found miscompiling passes!")
    print("*** Final File: %s" % sil_opt_invoker.input_file)
    print("*** Final Passes: %s" % (' '.join(r.target_list)))
    print("*** Repro command line: %s" % (' '.join(cmdline)))


def add_parser_arguments(parser):
    """Add parser arguments for opt_bug_reducer"""
    parser.set_defaults(func=pass_bug_reducer)
    parser.add_argument('input_file', help='The input file to optimize')
    parser.add_argument('--module-cache', help='The module cache to use')
    parser.add_argument('--sdk', help='The sdk to pass to sil-opt')
    parser.add_argument('--target', help='The target to pass to sil-opt')
    parser.add_argument('--resource-dir',
                        help='The resource-dir to pass to sil-opt')
    parser.add_argument('--work-dir',
                        help='Working directory to use for temp files',
                        default='bug_reducer')
    parser.add_argument('--module-name',
                        help='The name of the module we are optimizing')
    parser.add_argument('--pass', help='pass to test', dest='pass_list',
                        action='append')
    parser.add_argument('--extra-arg', help='extra argument to pass to sil-opt',
                        dest='extra_args', action='append')
