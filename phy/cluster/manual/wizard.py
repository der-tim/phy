# -*- coding: utf-8 -*-

"""Wizard."""

#------------------------------------------------------------------------------
# Imports
#------------------------------------------------------------------------------

import logging
from operator import itemgetter

from six import string_types

from ._history import History
from phy.utils._types import _as_tuple
from phy.utils import EventEmitter

logger = logging.getLogger(__name__)


#------------------------------------------------------------------------------
# Utility functions
#------------------------------------------------------------------------------

def _argsort(seq, reverse=True, n_max=None):
    """Return the list of clusters in decreasing order of value from
    a list of tuples (cluster, value)."""
    out = [cl for (cl, v) in sorted(seq,
                                    key=itemgetter(1),
                                    reverse=reverse)]
    if n_max in (None, 0):
        return out
    else:
        return out[:n_max]


def _next_in_list(l, item):
    if l and item in l and l.index(item) < len(l) - 1:
        return l[l.index(item) + 1]
    return item


def _sort(clusters, status=None, remove_ignored=False):
    """Sort clusters according to their status."""
    assert status
    _sort_map = {None: 0, 'good': 1, 'ignored': 2}
    if remove_ignored:
        clusters = [c for c in clusters if status(c) != 'ignored']
    # NOTE: sorted is "stable": it doesn't change the order of elements
    # that compare equal, which ensures that the order of clusters is kept
    # among any given status.
    key = lambda cluster: _sort_map.get(status(cluster), 0)
    return sorted(clusters, key=key)


def _best_clusters(clusters, quality, n_max=None):
    return _argsort([(cluster, quality(cluster))
                     for cluster in clusters], n_max=n_max)


def _most_similar_clusters(cluster, cluster_ids=None, n_max=None,
                           similarity=None, status=None, less_than=None):
    """Return the `n_max` most similar clusters to a given cluster."""
    if cluster not in cluster_ids:
        return []
    s = [(other, similarity(cluster, other))
         for other in cluster_ids
         if other != cluster]
    # Only keep values less than a threshold.
    if less_than:
        s = [(c, v) for (c, v) in s if v <= less_than]
    clusters = _argsort(s, n_max=n_max)
    return _sort(clusters, status=status, remove_ignored=True)


def _wizard_group(group):
    # The group should be None, 'mua', 'noise', or 'good'.
    assert group is None or isinstance(group, string_types)
    group = group.lower() if group else group
    if group in ('mua', 'noise'):
        return 'ignored'
    elif group == 'good':
        return 'good'
    return None


#------------------------------------------------------------------------------
# Strategy functions
#------------------------------------------------------------------------------

def best_quality_strategy(selection,
                          cluster_ids=None,
                          quality=None,
                          status=None,
                          similarity=None):
    """Two cases depending on the number of selected clusters:

    * 1: move to the next best cluster
    * 2: move to the next most similar pair
    * 3+: do nothing

    """
    if selection is None:
        return selection
    selection = _as_tuple(selection)
    n = len(selection)
    if n == 0 or n >= 3:
        return selection
    if n == 1:
        best_clusters = _best_clusters(cluster_ids, quality)
        # Sort the best clusters according to their status.
        best_clusters = _sort(best_clusters, status=status)
        return _next_in_list(best_clusters, selection[0])
    elif n == 2:
        best, match = selection
        value = similarity(best, match)
        candidates = _most_similar_clusters(best,
                                            cluster_ids=cluster_ids,
                                            similarity=similarity,
                                            status=status,
                                            less_than=value)
        if best in candidates:
            candidates.remove(best)
        if match in candidates:
            candidates.remove(match)
        if not candidates:
            return selection
        return (best, candidates[0])


#------------------------------------------------------------------------------
# Wizard
#------------------------------------------------------------------------------

class Wizard(EventEmitter):
    """Propose a selection of high-quality clusters and merge candidates.

    * The wizard is responsible for the selected clusters.
    * The wizard keeps no state about the clusters: the state is entirely
      provided by functions: cluster_ids, status (group), similarity, quality.
    * The wizard keeps track of the history of the selected clusters, but this
      history is cleared after every action that changes the state.
    * The `next()` function proposes a new selection as a function of the
      current selection only.
    * There are two strategies: best-quality or best-similarity strategy.

    TODO: cache expensive functions.

    """
    def __init__(self):
        super(Wizard, self).__init__()
        self._similarity = None
        self._quality = None
        self._get_cluster_ids = None
        self._cluster_status = lambda cluster: None
        self._next = None  # Strategy function.
        self.reset()

    def reset(self):
        self._selection = ()
        self._history = History(())

    # Quality and status functions
    #--------------------------------------------------------------------------

    def set_cluster_ids_function(self, func):
        """Register a function giving the list of cluster ids."""
        self._get_cluster_ids = func

    def set_status_function(self, func):
        """Register a function returning the status of a cluster: None,
        'ignored', or 'good'.

        Can be used as a decorator.

        """
        self._cluster_status = func
        return func

    def set_similarity_function(self, func):
        """Register a function returning the similarity between two clusters.

        Can be used as a decorator.

        """
        self._similarity = func
        return func

    def set_quality_function(self, func):
        """Register a function returning the quality of a cluster.

        Can be used as a decorator.

        """
        self._quality = func
        return func

    def set_strategy_function(self, func):
        """Register a function returning a new selection after the current
        selection, as a function of the quality and similarity of the clusters.
        """
        # func(selection, cluster_ids=None, quality=None, similarity=None)

        def wrapped(sel):
            return func(self._selection,
                        cluster_ids=self._get_cluster_ids(),
                        quality=self._quality,
                        status=self._cluster_status,
                        similarity=self._similarity,
                        )

        self._next = wrapped

    # Properties
    #--------------------------------------------------------------------------

    @property
    def cluster_ids(self):
        """Array of cluster ids in the current clustering."""
        return sorted(self._get_cluster_ids())

    @property
    def n_clusters(self):
        """Total number of clusters."""
        return len(self.cluster_ids)

    # Core methods
    #--------------------------------------------------------------------------

    def cluster_status(self, cluster):
        return self._cluster_status(cluster)

    # Selection methods
    #--------------------------------------------------------------------------

    @property
    def selection(self):
        """Return the current cluster selection."""
        return _as_tuple(self._selection)

    @selection.setter
    def selection(self, value):
        if value is None:  # pragma: no cover
            return
        clusters = self.cluster_ids
        value = tuple(cluster for cluster in value if cluster in clusters)
        self._selection = value
        self._history.add(self._selection)
        self.emit('select', self._selection)

    def select(self, cluster_ids):
        self.selection = cluster_ids

    @property
    def best(self):
        """Currently-selected best cluster."""
        return self._selection[0] if self._selection else None

    @property
    def match(self):
        """Currently-selected closest match."""
        return self._selection[1] if len(self._selection) >= 2 else None

    # Navigation
    #--------------------------------------------------------------------------

    def previous(self):
        if self._history.current_position <= 2:
            return self._selection
        self._history.back()
        sel = self._history.current_item
        if sel:
            self._selection = sel  # Not add this action to the history.
        return self._selection

    def next(self):
        if not self._history.is_last():
            # Go forward after a previous.
            self._history.forward()
            sel = self._history.current_item
            if sel:
                self._selection = sel  # Not add this action to the history.
        else:
            if self._next:
                # Or compute the next selection.
                self.selection = _as_tuple(self._next(self._selection))
            else:
                logger.debug("No strategy selected in the wizard.")
        return self._selection

    # Attach
    #--------------------------------------------------------------------------

    def attach(self, obj):
        """Attach an effector to the wizard."""

        # Save the current selection when an action occurs.
        @obj.connect
        def on_request_undo_state(up):
            return {'selection': self._selection}

        @obj.connect
        def on_cluster(up):
            if up.history == 'undo':
                # Revert to the given selection after an undo.
                self._selection = tuple(up.undo_state[0]['selection'])
            else:
                # Or move to the next selection after any other action.
                self.next()
