'''
Defines functions for making the population.
'''

# %% Imports
from re import U
import numpy as np  # Needed for a few things not provided by pl
import sciris as sc
from . import utils as cellUtil
from . import misc as cellMisc
# from . import base as cvb
from . import default as cellDef
from . import parameters as cellPar
from . import cell_mass as cellMass


# # Specify all externally visible functions this file defines
# __all__ = ['make_people', 'make_randpop', 'make_random_contacts']


def make_people(sim, popdict=None, reset=False, verbose=None, use_age_data=True,
                sex_ratio=0.5, dt_round_age=True, dispersion=None, microstructure=None, **kwargs):
    '''
    Make the people for the simulation.

    Usually called via ``sim.initialize()``.

    Args:
        sim      (Sim)  : the simulation object; population parameters are taken from the sim object
        popdict  (any)  : if supplied, use this population dictionary instead of generating a new one; can be a dict or People object
        reset    (bool) : whether to force population creation even if self.popdict/self.people exists
        verbose  (bool) : level of detail to print
        use_age_data (bool):
        sex_ratio (bool):
        dt_round_age (bool): whether to round people's ages to the nearest timestep (default true)

    Returns:
        people (People): people
    '''

    # Set inputs and defaults
    pop_size = int(sim['pop_size'])  # Shorten
    if verbose is None:
        verbose = sim['verbose']
    dt = sim['dt']  # Timestep

    # If a people object or popdict is supplied, use it
    if sim.people and not reset:
        sim.people.initialize(sim_pars=sim.pars)
        return sim.people  # If it's already there, just return
    elif sim.popdict and popdict is None:
        popdict = sim.popdict  # Use stored one
        sim.popdict = None  # Once loaded, remove

    if popdict is None:

        pop_size = int(sim['pop_size'])  # Number of people

        # Load age data by country if available, or use defaults.
        # Other demographic data like mortality and fertility are also available by
        # country, but these are loaded directly into the sim since they are not
        # stored as part of the people.
        age_data =  cellDef.default_age_data
        location = sim['location']

        uids, sexes, debuts, partners = set_static(pop_size, pars=sim.pars, sex_ratio=sex_ratio, dispersion=dispersion)

        # Set ages, rounding to nearest timestep if requested
        age_data_min = age_data[:, 0]
        age_data_max = age_data[:, 1] + 1  # Since actually e.g. 69.999
        age_data_range = age_data_max - age_data_min
        age_data_prob = age_data[:, 2]
        age_data_prob /= age_data_prob.sum()  # Ensure it sums to 1
        age_bins = cellUtil.n_multinomial(age_data_prob, pop_size)  # Choose age bins
        if dt_round_age:
            ages = age_data_min[age_bins] + np.random.randint(
                age_data_range[age_bins] / dt) * dt  # Uniformly distribute within this age bin
        else:
            ages = age_data_min[age_bins] + age_data_range[age_bins] * np.random.random(
                pop_size)  # Uniformly distribute within this age bin

        # Store output
        popdict = {}
        popdict['uid'] = uids
        popdict['age'] = ages
        popdict['sex'] = sexes
        popdict['debut'] = debuts
        popdict['partners'] = partners

        # Create the contacts
        active_inds = cellUtil.true(ages > debuts)  # Indices of sexually experienced people
        if microstructure in ['random', 'basic']:
            contacts = dict()
            current_partners = []
            lno = 0
            for lkey, n in sim['partners'].items():
                active_inds_layer = cellUtil.binomial_filter(sim['layer_probs'][lkey], active_inds)
                durations = sim['dur_pship'][lkey]
                acts = sim['acts'][lkey]
                contacts[lkey], cp = make_random_contacts(p_count=partners[lno], sexes=sexes, ages=ages, n=n,
                                                          durations=durations, acts=acts, mapping=active_inds_layer,
                                                          **kwargs)
                current_partners.append(cp)
                lno += 1
        else:
            errormsg = f'Microstructure type "{microstructure}" not found; choices are random or TBC'
            raise NotImplementedError(errormsg)

        popdict['contacts'] = contacts
        popdict['current_partners'] = np.array(current_partners)
        popdict['layer_keys'] = list(sim['partners'].keys())

    # Ensure prognoses are set
    if sim['prognoses'] is None:
        sim['prognoses'] = cellPar.get_prognoses()

    # Do minimal validation and create the people
    validate_popdict(popdict, sim.pars, verbose=verbose)
    people = cellMass.People(sim.pars, uid=popdict['uid'], age=popdict['age'], sex=popdict['sex'], debut=popdict['debut'],
                          partners=popdict['partners'], contacts=popdict['contacts'],
                          current_partners=popdict['current_partners'])  # List for storing the people
    people.age_brackets = np.digitize(people.age,
                                      cellDef.age_brackets) + 1  # Store which age bucket people belong to, adding 1 so there are no zeros

    sc.printv(f'Created {pop_size} people, average age {people.age.mean():0.2f} years', 2, verbose)

    return people


def partner_count(pop_size=None, layer_keys=None, means=None, sample=True, dispersion=None):
    '''
    Assign each person a preferred number of concurrent partners for each layer
    Args:
        pop_size    (int)   : number of people
        layer_keys  (list)  : list of layers
        means       (dict)  : dictionary keyed by layer_keys with mean number of partners per layer
        sample      (bool)  : whether or not to sample the number of partners
        dispersion  (any)   : if not None, will use negative binomial sampling

    Returns:
        p_count (dict): the number of partners per person per layer
    '''

    # Initialize output
    partners = []

    # If means haven't been supplied, set to zero
    if means is None:
        means = {k: np.zeros(pop_size) for k in layer_keys}
    else:
        if len(means) != len(layer_keys):
            errormsg = f'The list of means has length {len(means)}; this must be the same length as layer_keys ({len(layer_keys)}).'
            raise ValueError(errormsg)

    # Now set the number of partners
    for lkey, n in zip(layer_keys, means):
        if sample:
            if dispersion is None:
                p_count = cellUtil.n_poisson(n,
                                        pop_size) + 1  # Draw the number of Poisson partners for this person. TEMP: add 1 to avoid zeros
            else:
                p_count = cellUtil.n_neg_binomial(rate=n, dispersion=dispersion,
                                             n=pop_size) + 1  # Or, from a negative binomial
        else:
            p_count = np.full(pop_size, n, dtype= cellDef.default_int)

        partners.append(p_count)

    return np.array(partners)


def set_static(new_n, existing_n=0, pars=None, sex_ratio=0.5, dispersion=None):
    '''
    Set static population characteristics that do not change over time.
    Can be used when adding new births, in which case the existing popsize can be given.
    '''
    uid = np.arange(existing_n, existing_n + new_n, dtype=cellDef.default_int)
    sex = np.random.binomial(1, sex_ratio, new_n)
    debut = np.full(new_n, np.nan, dtype= cellDef.default_float)
    debut[sex == 1] = cellUtil.sample(**pars['debut']['m'], size=sum(sex))
    debut[sex == 0] = cellUtil.sample(**pars['debut']['f'], size=new_n - sum(sex))
    partners = partner_count(pop_size=new_n, layer_keys=pars['partners'].keys(), means=pars['partners'].values(),
                             dispersion=dispersion)
    return uid, sex, debut, partners


def validate_popdict(popdict, pars, verbose=True):
    '''
    Check that the popdict is the correct type, has the correct keys, and has
    the correct length
    '''

    # Check it's the right type
    try:
        popdict.keys()  # Although not used directly, this is used in the error message below, and is a good proxy for a dict-like object
    except Exception as E:
        errormsg = f'The popdict should be a dictionary or hp.People object, but instead is {type(popdict)}'
        raise TypeError(errormsg) from E

    # Check keys and lengths
    required_keys = ['uid', 'age', 'sex', 'debut']
    popdict_keys = popdict.keys()
    pop_size = pars['pop_size']
    for key in required_keys:

        if key not in popdict_keys:
            errormsg = f'Could not find required key "{key}" in popdict; available keys are: {sc.strjoin(popdict.keys())}'
            sc.KeyNotFoundError(errormsg)

        actual_size = len(popdict[key])
        if actual_size != pop_size:
            errormsg = f'Could not use supplied popdict since key {key} has length {actual_size}, but all keys must have length {pop_size}'
            raise ValueError(errormsg)

        isnan = np.isnan(popdict[key]).sum()
        if isnan:
            errormsg = f'Population not fully created: {isnan:,} NaNs found in {key}.'
            raise ValueError(errormsg)

    return




# %%
