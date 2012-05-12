import sys
import os
import math
import numbers
import traceback
import csv
import collections
import textwrap
import pprint
import configfile
import itertools
import logging
import tempfile
import contextlib

import cmd_completer
import vector

float_nan = float("nan")

def printf(fmt, *args, **kwargs):
    print(fmt.format(*args, **kwargs))

@contextlib.contextmanager
def Umask(umask):
    old = os.umask(umask)
    try:
        yield
    finally:
        os.umask(old)


DUMP_FMT = '''\
name: {p.name} {p.lastname} <{p.email}>
born: {p.nation} {p.born}
gender: {p.gender}
institute: {p.institute}
group: {p.group}
country: {p.country}
position: {p.position}{position_other}
appl.prev.: {p.applied}
programming: {p.programming}{programming_description} [{programming_score}]
python: {p.python} [{python_score}]
open source: {p.open_source}{open_source_description} [{open_source_score}]
cv: {cv} [{cv_scores}]
motivation: {motivation} [{motivation_scores}]
rank: {p.rank} {p.score}
'''

RANK_FMT_LONG = ('{: 4} {p.rank: 4} {p.score:6.3f}'
                 ' {p.fullname:{fullname_width}} {email:{email_width}}'
                 ' {p.institute:{institute_width}} / {p.group:{group_width}}')
RANK_FMT_SHORT = ('{: 4} {p.rank: 4} {p.score:6.3f}'
                 ' {p.fullname:{fullname_width}} {email:{email_width}}')


SCORE_RANGE = (-1, 0, 1)

IDENTITIES = (0, 1)

HOST_COUNTRY = 'Germany'

DEFAULT_ACCEPT_COUNT = 30

section_name = '{}_score-{}'.format

class Grader(cmd_completer.Cmd_Completer):
    prompt = 'grader> '
    set_completions = cmd_completer.Cmd_Completer.set_completions
    HISTFILE = '~/.grader_history'

    def __init__(self, identity, config, applications):
        super().__init__(histfile=self.HISTFILE)

        self.identity = identity
        self.config = config
        self.applications = csv_file(applications,
                                     self.Person(self.application_fields))

        self.modified = False

    @classmethod
    def Person(cls, names):
        class Person(collections.namedtuple('Person', names)):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.score = None
                self.rank = None
            @property
            def fullname(self):
                return '{p.name} {p.lastname}'.format(p=self)
        return Person

    @property
    def application_fields(self):
        return """id completed last_page_seen start_language
                  date_last_action date_started
                  ip_address referrer
                  nation born gender
                  institute group country
                  position position_other
                  applied
                  programming python programming_description
                  open_source open_source_description
                  motivation cv
                  name lastname email
                  token""".split()

    @property
    def formula(self):
        try:
            return self.config['formula']['formula']
        except KeyError:
            return None
    @formula.setter
    def formula(self, value):
        # check syntax
        compile(value, '--formula--', 'eval')
        self.config['formula']['formula'] = value

    @property
    def accept_count(self):
        return int(self.config['formula'].create('accept_count',
                                                 lambda:DEFAULT_ACCEPT_COUNT))
    @accept_count.setter
    def accept_count(self, value):
        self.config['formula']['accept_count'] = value

    @property
    def programming_rating(self):
        return self.config['programming_rating']
    @property
    def open_source_rating(self):
        return self.config['open_source_rating']
    @property
    def python_rating(self):
        return self.config['python_rating']

    def _complete_name(self, prefix):
        """Return a list of dictionaries {name -> [last-name+]}

        Name or last-name must start with prefix.
        """
        completions = collections.defaultdict(set)
        for p in self.applications:
            if p.name.startswith(prefix) or p.lastname.startswith(prefix):
                completions[p.name].add(p.lastname)
        return completions

    dump_options = cmd_completer.ModArgumentParser('dump')\
        .add_argument('-l', '--long', action='store_const',
                      dest='format', const='long', default='short',
                      help='do not truncate free texts')\
        .add_argument('-r', '--ranked', action='store_true',
                      help='print applications sorted by rank')\
        .add_argument('persons', nargs='*',
                      help='name fragments of people do display')

    def do_dump(self, args):
        "Print information about applications"
        opts = self.dump_options.parse_args(args.split())
        if opts.persons:
            persons = (p for p in self.applications
                       if any(arg in p.fullname for arg in opts.persons))
        else:
            persons = self.applications
        if opts.ranked:
            persons = self._ranked(persons)
        self._dump(persons, format=opts.format)

    do_dump.completions = _complete_name

    def _dump(self, persons, format='short'):
        for p in persons:
            self._dumpone(p, format=format)

    def _dumpone(self, p, format='short'):
        position_other = \
            (' ({})'.format(p.position_other) if p.position=='Other' else '')
        if format == 'short':
            pd = p.programming_description.replace('\n', ' ')[:72]
            osd = p.open_source_description.replace('\n', ' ')[:72]
            cv = p.cv.replace('\n', ' ')[:72]
            motivation = p.motivation.replace('\n', ' ')[:72]
        elif format == 'long':
            pd = wrap_paragraphs(p.programming_description) + '\n'
            osd = wrap_paragraphs(p.open_source_description) + '\n'
            cv = wrap_paragraphs(p.cv) + '\n'
            motivation = wrap_paragraphs(p.motivation) + '\n'
        else:
            raise KeyError("unknown format '{}'".format(format))
        programming_description = ('\nprogramming: {}'.format(pd)
                                   if p.programming_description else '')
        open_source_description = ('\nopen source: {}'.format(osd)
                                   if p.open_source_description else '')
        printf(DUMP_FMT,
               p=p,
               position_other=position_other,
               programming_description=programming_description,
               open_source_description=open_source_description,
               programming_score=\
                   get_rating('programming', self.programming_rating,
                              p.programming, '-'),
               open_source_score=\
                   get_rating('open_source', self.open_source_rating,
                              p.open_source, '-'),
               python_score=\
                   get_rating('python', self.python_rating, p.python, '-'),
               cv=cv,
               motivation=motivation,
               cv_scores=self._gradings(p, 'cv'),
               motivation_scores=self._gradings(p, 'motivation'),
               )

    def do_grep(self, args):
        "Look for string in applications"
        if args.split()[0] == '-l':
            format = 'long'
            args = args[args.index('-l')+2:].lstrip()
        else:
            format='short'

        self._dump((p for p in self.applications
                    if args in str(p)), format=format)

    grade_options = cmd_completer.ModArgumentParser('grade')\
        .add_argument('what', choices=['motivation', 'cv', 'formula'],
                      help='what to grade | set formula')\
        .add_argument('-g', '--graded', action='store_true',
                      help='grade already graded too')\
        .add_argument('person', nargs='*')

    @set_completions('formula',
                     motivation=_complete_name,
                     cv=_complete_name)
    def do_grade(self, arg):
        """Assign points to motivation or CV statements or set formula

        Formula is set with:
          grade formula ...
        where ... is a python expression using the following variables:
          born: int,
          gender: 'M' or 'F',
          female: 0 or 1,
          nation: str,
          country: str,
          motivation: float,
          cv: float,
          programming: float,
          open_source: float,
          applied: 0 or 1,
          python: float.
        """
        if self.identity is None:
            raise ValueError('cannot do grading because identity was not set')

        opts = self.grade_options.parse_args(arg.split())
        if opts.graded and opts.person:
            raise ValueError('cannot use --graded option with explicit name')

        if opts.what == 'formula':
            if opts.person:
                self.formula = ' '.join(opts.person)
                self.modified = True
            minsc, maxsc = find_min_max(self.formula,
                                        self.programming_rating,
                                        self.open_source_rating,
                                        self.python_rating)


            printf('formula = {}', self.formula)
            printf('score ∈ [{:6.3f},{:6.3f}]', minsc, maxsc)
            return

        printf('Doing grading for identity {}', self.identity)
        print('Press ^C or ^D to stop')
        fullname = ' '.join(opts.person)

        todo = [p for p in self.applications
                if (p.fullname == fullname if fullname
                    else
                    opts.graded or
                    self._get_grading(p, opts.what) is None)]
        done_already = len(self.applications) - len(todo)
        for num, person in enumerate(todo):
            printf('{:.2f}% done, {} left to go',
                   100*(num+done_already)/len(self.applications),
                   len(todo)-num)
            if not self._grade(person, opts.what):
                break

    RATING_CATEGORIES = ['programming', 'open_source', 'python']

    rate_options = cmd_completer.ModArgumentParser('rate')\
        .add_argument('-m', '--missing', action='store_true',
                      help='rate all missing fields')\
        .add_argument('what', nargs='?',
                      choices=RATING_CATEGORIES)\
        .add_argument('args', nargs='*')

    @set_completions(*RATING_CATEGORIES)
    def do_rate(self, arg):
        "Get rating for activity or set to some value"
        opts = self.rate_options.parse_args(arg.split())
        if opts.missing and opts.args:
            raise SyntaxError('cannot use -m with arguments')

        if opts.what is None:
            whats = self.RATING_CATEGORIES
        else:
            whats = [opts.what]

        for what in whats:
            if opts.what is None:
                printf('== {} ==', what)
            section = what + '_rating'
            current = self.config[section]
            if opts.args:
                how = ' '.join(opts.args[:-1])
                value = float(opts.args[-1])
                current[how] = value
                self.modified = True
            else:
                current.print_sorted()
                if opts.missing:
                    used = set(getattr(p, what).lower()
                               for p in self.applications)
                    for descr in used:
                        try:
                            get_rating(what, current, descr)
                        except MissingRating as e:
                            raw = input('{} = '.format(descr))
                            value = float(raw)
                            current[e.key] = value
                            self.modified = True

    def _get_grading(self, person, what):
        section = self.config[section_name(what, self.identity)]
        return section.get(person.fullname, None)

    def _gradings(self, person, what):
        gen = (
            self.config[section_name(what, identity)].get(person.fullname, None)
            for identity in IDENTITIES)
        return list_of_float(gen)

    def _set_grading(self, person, what, score):
        assert isinstance(score, numbers.Number), score
        section = self.config[section_name(what, self.identity)]
        section[person.fullname] = score
        printf('{} score set to {}', what, score)
        self.modified = True

    def _grade(self, person, what):
        assert what in {'motivation', 'cv'}, what
        text = getattr(person, what)
        old_score = self._get_grading(person, what)
        default = old_score if old_score is not None else ''
        printf('{line}\n{}\n{line}', wrap_paragraphs(text), line='-'*70)
        printf('Old score was {}', old_score)
        while True:
            prompt = 'Your choice {} [{}]? '.format(SCORE_RANGE, default)
            try:
                choice = input(prompt)
            except EOFError:
                print()
                return False
            if choice == 's':
                print('person skipped')
                return True
            elif choice == 'd':
                print('showing person on request')
                self._dumpone(person, format='long')
                continue
            elif choice == '':
                choice = default
            if choice == '+':
                choice = SCORE_RANGE[-1]
            elif choice == '-':
                choice = SCORE_RANGE[0]
            try:
                choice = int(choice)
                if choice not in SCORE_RANGE:
                    raise ValueError('illegal value: {}'.format(choice))
            except ValueError as e:
                print(e)
            else:
                break
        if choice != default:
            self._set_grading(person, what, choice)
        return True

    def _assign_rankings(self):
        "Order applications by rank"
        if self.formula is None:
            raise ValueError('formula not set yet')

        minsc, maxsc = find_min_max(self.formula,
                                    self.programming_rating,
                                    self.open_source_rating,
                                    self.python_rating)

        for person in self.applications:
            person.score = rank_person(person, self.formula,
                                       self.programming_rating,
                                       self.open_source_rating,
                                       self.python_rating,
                                       self._gradings(person, 'motivation'),
                                       self._gradings(person, 'cv'),
                                       minsc, maxsc)
        ranked = sorted(self.applications, key=lambda p: p.score, reverse=True)

        labs = {}
        rank = 0
        for person in ranked:
            group = self._equiv_master(person.group)
            institute = self._equiv_master(person.institute)
            lab = institute + ' / ' + group
            if lab not in labs:
                labs[lab] = rank
                rank += 1
            person.rank = labs[lab]
        pprint.pprint(labs)

    def _ranked(self, applications=None):
        if applications is None:
            applications = self.applications

        ranked = sorted(applications, key=lambda p: p.rank)
        return vector.vector(ranked)

    def _equiv_master(self, variant):
        "Return the key for equiv canocalization"
        for key, values in self.config['equivs'].items():
            if (variant.lower() == key.lower() or
                variant.lower() in (spelling.lower() for spelling in values)):
                return key
        return variant.strip()

    rank_options = cmd_completer.ModArgumentParser('rank')\
        .add_argument('-s', '--short', action='store_const',
                      dest='format', const='short', default='long',
                      help='show only names and emails')

    def do_rank(self, args):
        "Print list of people sorted by ranking"
        opts = self.rank_options.parse_args(args.split())
        self._assign_rankings()
        ranked = self._ranked()
        fullname_width = max(len(field) for field in ranked.fullname)
        email_width = max(len(field) for field in ranked.email)
        institute_width = min(max(len(field) for field in ranked.institute), 20)
        group_width = min(max(len(field) for field in ranked.group), 20)
        for pos, person in enumerate(ranked):
            if person.rank == self.accept_count:
                print('-' * 70)
            if opts.format == 'short':
                printf(RANK_FMT_SHORT, pos, p=person,
                       email='<{}>'.format(person.email),
                       fullname_width=fullname_width, email_width=email_width)
            else:
                printf(RANK_FMT_LONG, pos, p=person,
                       email='<{}>'.format(person.email),
                       fullname_width=fullname_width, email_width=email_width,
                       institute_width=institute_width, group_width=group_width)

    def do_equiv(self, args):
        "Specify institutions'/labs' names as equivalent"
        if args == '':
            for key, value in self.config['equivs'].items():
                printf('{} = {}', key, value)
            return

        variant, *equivs = [item.strip() for item in args.split('=')]
        saved = self.config['equivs'].get(variant, list_of_equivs())
        saved.extend(equivs)
        self.config['equivs'][variant] = saved
        self.modified = True

    save_options = cmd_completer.ModArgumentParser('save')\
        .add_argument('filename', nargs='?')

    def do_save(self, args):
        "Save the fruits of thy labour"
        opts = self.save_options.parse_args(args.split())
        self.config.save(opts.filename)
        self.modified = False

    def do_write(self, args):
        "Write lists of mailing ricipients"
        if args != '':
            raise ValueError('no args please')
        ranked = self._ranking()
        printf('accepting {}', self.accept_count)
        count = collections.Counter(ranked.rank)

        _write_file('applications_accepted.csv',
                    (person for person in ranked if
                     person.rank < self.accept_count and count[person.rank] == 1))
        _write_file('applications_same_lab.csv',
                    (person for person in ranked if
                     person.rank < self.accept_count and count[person.rank] != 1))
        _write_file('applications_rejected.csv',
                    (person for person in ranked if
                     person.rank >= self.accept_count))


def _write_file(filename, persons):
    header = '$NAME$;$SURNAME$;$EMAIL$'
    with open(filename, 'w') as f:
        f.write(header + '\n')
        i = 0
        for i, person in enumerate(persons):
            row = ';'.join((person.name, person.lastname, person.email))
            f.write(row + '\n')
    printf("'{}' written with header + {} rows", filename, i+1)

def eval_formula(formula, vars):
    try:
        return eval(formula, vars, {})
    except (NameError, TypeError) as e:
        vars.pop('__builtins__', None)
        msg = 'formula failed: {}\n[{}]\n[{}]'.format(e, formula,
                                                      pprint.pformat(vars))
        raise ValueError(msg)

class MissingRating(KeyError):
    def __str__(self):
        return '{} not rated for {}'.format(*self.args)
    @property
    def key(self):
        return self.args[1]

def get_rating(name, dict, key, fallback=None):
    """Retrieve rating.

    Explanation in () or after / is ignored in the key.

    Throws ValueError is rating is not present.
    """
    key = key.partition('(')[0].partition('/')[0].strip()
    try:
        return dict[key]
    except KeyError:
        if fallback is None:
            raise MissingRating(name, key)
            # raise ... from None, when implemented!
        else:
            return fallback

class list_of_float(list):
    def __str__(self):
        return ', '.join(str(item) if item is not None else '-'
                         for item in self)

    def mean(self):
        valid = [arg for arg in self if arg is not None]
        if not valid:
            return float_nan
        return sum(valid) / len(valid)

def rank_person(person, formula,
                programming_rating, open_source_rating, python_rating,
                motivation_scores, cv_scores, minsc, maxsc):
    "Apply formula to person and return score"
    vars = {}
    for attr, dict in zip(('programming', 'open_source', 'python'),
                          (programming_rating, open_source_rating, python_rating)):
        key = getattr(person, attr)
        value = get_rating(attr, dict, key)
        vars[attr] = value
    fullname = person.fullname
    vars.update(born=int(person.born), # if we decide to implement ageism
                gender=person.gender, # if we decide, ...
                                      # oh we already did
                female=(person.gender == 'Female'),
                applied=(person.applied[0] not in 'nN'),
                nation=person.nation,
                country=person.country,
                motivation=motivation_scores.mean(),
                cv=cv_scores.mean(),
                email=person.email, # should we discriminate against gmail?
                )
    score = eval_formula(formula, vars)
    assert math.isnan(score) or minsc <= score <= maxsc, (minsc, score, maxsc)
    # XXX: Remove scaling until we find a better solution to compare
    #      different formulas
    # scale linearly to SCORE_RANGE/min/max
    #range = max(SCORE_RANGE) - min(SCORE_RANGE)
    #offset = min(SCORE_RANGE)
    #score = (score - minsc) / (maxsc - minsc) * range + offset
    return score

def _yield_values(var, *values):
    for value in values:
        yield var, value

def find_min_max(formula,
                 programming_rating, open_source_rating, python_rating):
    # coordinate with rank_person!
    options = itertools.product(
        _yield_values('born', 1900, 2012),
        _yield_values('gender', 'M', 'F'),
        _yield_values('female', 0, 1),
        _yield_values('nation', 'Nicaragua', HOST_COUNTRY),
        _yield_values('country', 'Nicaragua', HOST_COUNTRY),
        _yield_values('motivation', *SCORE_RANGE),
        _yield_values('cv', *SCORE_RANGE),
        _yield_values('programming', *programming_rating.values()),
        _yield_values('open_source', *open_source_rating.values()),
        _yield_values('applied', 0, 1),
        _yield_values('python', *python_rating.values()),
        )
    values = [eval_formula(formula, dict(vars)) for vars in options]
    if not values:
        return float_nan, float_nan
    return min(values), max(values)

def wrap_paragraphs(text):
    paras = text.strip().split('\n\n')
    wrapped = ('\n'.join(textwrap.wrap(para)) for para in paras)
    return '\n\n'.join(wrapped)

@vector.vectorize
def csv_file(file, tuple_factory):
    reader = csv.reader(file)
    header = next(reader)
    assert len(header) == len(tuple_factory._fields)
    while True:
        yield tuple_factory(*next(reader))

class list_of_equivs(list):
    def __init__(self, arg=None):
        equivs = ((item.strip() for item in arg.split('='))
                  if arg is not None else ())
        super().__init__(equivs)

    def __str__(self):
        return ' = '.join(self)

def our_configfile(filename):
    kw = {section_name(what, ident):float
          for what in ('motivation', 'cv')
          for ident in IDENTITIES}
    return configfile.ConfigFile(filename,
                                 programming_rating=float,
                                 open_source_rating=float,
                                 python_rating=float,
                                 formula=str,
                                 equivs=list_of_equivs,
                                 **kw)

def open_no_newlines(filename):
    return open(filename, newline='')

grader_options = cmd_completer.ModArgumentParser('grader')\
    .add_argument('applications', type=open_no_newlines,
                  help='CSV file with application data')\
    .add_argument('config', type=our_configfile)\
    .add_argument('-i', '--identity', type=int,
                  choices=IDENTITIES,
                  help='Index of person grading applications')

def main(argv0, *args):
    logging.basicConfig(level=logging.INFO)

    opts = grader_options.parse_args(args)
    cmd = Grader(opts.identity, opts.config, opts.applications)

    if sys.stdin.isatty():
        while True:
            try:
                cmd.cmdloop()
                break
            except KeyboardInterrupt:
                print()
            except SyntaxError as e:
                printf('bad command: {}', e)
            except ValueError as e:
                printf('bad value: {}', e)
                traceback.print_exc()
            except Exception as e:
                printf('programming error: {}', e)
                traceback.print_exc()
    else:
        input = cmd_completer.InputFile(sys.stdin)
        for line in input:
            cmd.onecmd(line)

    if cmd.modified:
        print("It seems thy labours' fruits may be going into oblivion...")
        with Umask(0o077):
            tmpfile = tempfile.mkstemp(prefix='grader-', suffix='.conf')[1]
            printf("Saving them to {} instead", tmpfile)
            cmd.do_save(tmpfile)

if __name__ == '__main__':
    sys.exit(main(*sys.argv))
