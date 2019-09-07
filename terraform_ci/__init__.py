"""Helper functions to run terraform in CI or workstation."""
import json
import logging
from os import environ, path as osp
from subprocess import Popen, PIPE

__version__ = '0.1.2'

DEFAULT_TERRAFORM_VARS = '.env/tf_env.json'
LOG = logging.getLogger(__name__)


def render_comment(status):
    """
    Format status with markdown syntax to publish it as a comment.

    :param status: Status generated by a series of terraform plan.
        For example::

        {'cloudflare': {'add': 0,
                        'change': 0,
                        'destroy': 0,
                        'stderr': b'',
                        'success': True},
         'github': {'add': 0,
                    'change': 0,
                    'destroy': 0,
                    'stderr': b'',
                    'success': True},
         'management_app': {'add': 0,
                            'change': 0,
                            'destroy': 0,
                            'stderr': b'',
                            'success': True},
         'prod/recovery_app': {'add': 0,
                               'change': 0,
                               'destroy': 0,
                               'stderr': b'',
                               'success': True},
         'prod/web_app': {'add': 0,
                          'change': 0,
                          'destroy': 0,
                          'stderr': b'',
                          'success': True},
         'stage/recovery_app2': {'add': 0,
                                 'change': 0,
                                 'destroy': 0,
                                 'stderr': b'',
                                 'success': True},
         'stage/web_app': {'add': 0,
                           'change': 0,
                           'destroy': 0,
                           'stderr': b'',
                           'success': True}}
    :type status: dict
    :return: Markdown formatted comment
    :rtype: str
    """
    map_change = {
        'add': '![#c5f015](https://placehold.it/15/c5f015/000000?text=+) ',
        'change': '![#1589F0](https://placehold.it/15/1589F0/000000?text=+) ',
        'destroy': '![#f03c15](https://placehold.it/15/f03c15/000000?text=+) '
    }

    def flag(local_change):
        for k in status.keys():
            if status[k][local_change] > 0:
                return map_change[local_change]

        return ''

    comment = " | ".join(
        [
            'Module',
            'Success',
            flag('add') + 'Add',
            flag('change') + 'Change',
            flag('destroy') + 'Destroy'
        ]
    )
    comment += '\n' + "--- | --- | ---: | ---: | ---:" + '\n'

    tag_map = {
        True: '![#c5f015](https://placehold.it/15/c5f015/000000?text=+)',
        False: '![#f03c15](https://placehold.it/15/f03c15/000000?text=+)'
    }
    for key in status.keys():
        changes = {}
        for change in ['add', 'change', 'destroy']:
            if status[key][change] > 0:
                changes[change] = '**%d**' % status[key][change]
            else:
                changes[change] = status[key][change]

        line = "**{module}** | {tag} `{success}` " \
               "| {add} | {change} | {destroy}"
        line = line.format(
            module=key,
            tag=tag_map[status[key]['success']],
            success=status[key]['success'],
            add=changes['add'],
            change=changes['change'],
            destroy=changes['destroy']
        )
        comment += line + '\n'
    for key in status.keys():
        outs = {}
        for out in ['stdout', 'stderr']:
            if isinstance(status[key][out], bytes):
                outs[out] = status[key][out].decode('utf-8')
            else:
                outs[out] = status[key][out]

        line = """
# **{module}**

## stdout

{cout}

## stderr

{cerr}
""".format(module=key,
           cout='```' + outs['stdout'] + '```'
           if outs['stdout'] else '_no output_',
           cerr='```' + outs['stderr'] + '```'
           if outs['stderr'] else '_no output_',
           )
        comment += line

    return comment


def get_action(branch=None, pull_request=False):
    """
    Detect terraform action based on input branch and pull_request flag.
    If it cannot detect the action (branch is not given or error) the action
    will be ``plan``.

    :param branch: Branch name.
    :type branch: str
    :param pull_request: Whether the branch is a pull request.
    :type pull_request: bool
    :return: "apply" or "plan". It will return "apply" only if the branch is
        "master" and not a pull request.
    :rtype: str
    """
    if branch == "master" and not pull_request:
        return 'apply'

    return 'plan'


def parse_plan(output):
    """
    Parse a string given by output and return a tuple with execution plan.

    :param output: Output of terraform plan command.
    :type output: str
    :return: Tuple with number of changes (add, change, destroy)
    :rtype: tuple
    """
    add = None
    change = None
    destroy = None
    try:
        for line in output.splitlines():
            if line.startswith('Plan: '):
                split_line = line.split()
                # Plan: 4 to add, 11 to change, 7 to destroy.
                add = int(split_line[1])
                change = int(split_line[4])
                destroy = int(split_line[7])
            elif line == "No changes. Infrastructure is up-to-date.":
                return 0, 0, 0

    except AttributeError:
        pass

    return add, change, destroy


def run_job(path, action):
    """
    Run a job for a given module specified by path.

    :param path: Path to terraform module.
    :type path: str
    :param action: "apply" or "plan". Other action are not supported.
    :return: Dictionary with run report:

        {
            'success': True or False
            'add': x,
            'change': x,
            'destroy': x,
            'raw': <original content of the plan output>
        }
    :rtype: dict
    """
    returncode, cout, cerr = execute(
        [
            'make',
            '-C', path,
            action
        ]
    )
    status = {
        'success': returncode == 0,
        'stderr': cerr,
        'stdout': cout
    }
    parse_tree = parse_plan(cout.decode('utf-8'))
    status['add'] = parse_tree[0]
    status['change'] = parse_tree[1]
    status['destroy'] = parse_tree[2]

    return status


def execute(cmd):
    """
    Execute a command and return a tuple with return code, STDOUT and STDERR.

    :param cmd: Command.
    :type cmd: list
    :return: Tuple (return code, STDOUT, STDERR)
    :rtype: tuple
    """
    LOG.info('Executing: %s', ' '.join(cmd))
    proc = Popen(cmd, stdout=PIPE, stderr=PIPE)
    cout, cerr = proc.communicate()
    return proc.returncode, cout, cerr


def setup_environment(config_path=DEFAULT_TERRAFORM_VARS):
    """
    Read AWS variables from Terraform config and set them
    as environment variables
    """
    with open(config_path) as f_descr:
        tf_vars = json.loads(f_descr.read())

    common_variables = [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN"
    ]
    for variable in common_variables:
        try:
            environ[variable] = tf_vars['TF_VAR_{var}'.format(
                var=variable.upper()
            )]
        except KeyError:
            pass

    for key, value in tf_vars.items():
        environ[key] = value


def module_name_from_path(path):
    """
    Get one level up directory and return it as module name

    :param path: Path to directory
    :return: parent directory
    :rtype: str
    """
    abspath = osp.abspath(path)

    if abspath == '/':
        return 'root'

    return abspath.split(sep=osp.sep)[-1]


def convert_to_newlines(text):
    """
    Convert \n in the bytes ``text`` into actual new lines.

    :param text: Input string where new lines are encoded as ``\n``
    :type text: bytes
    :return: Text where \n are replaced with actual new lines.
    :rtype: str
    """
    return text.replace(b'\\n', b'\n').decode('UTF-8')
