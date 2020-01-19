from functools import partial
from .ops import Ops
import numpy
from ..types import Array, Array2d, Array1d, ArrayT, DTypes, Array3d, Wrapper
from ..types import Padded
from typing import Sequence, Optional, List, Tuple, Callable, cast

try:  # pragma: no cover
    import jax
    import jax.ops
    import jax.random
    import jax.tree_util
    from jax.ops import index_update, index

    has_jax = True
except ImportError:  # pragma: no cover
    has_jax = False


class JaxOps(Ops):
    xp = jax.numpy if has_jax else None

    def as_contig(self, data: ArrayT, dtype: Optional[DTypes] = None) -> ArrayT:
        return data if dtype is None else data.astype(dtype)

    def to_numpy(self, data):
        if isinstance(data, numpy.ndarray):
            return data
        else:
            return jax.device_get(data)

    def seq2col(self, seq: ArrayT, nW: int) -> ArrayT:
        """Given an (M, N) sequence of vectors, return an (M, N*(nW*2+1))
        sequence. The new sequence is constructed by concatenating nW preceding
        and succeeding vectors onto each column in the sequence, to extract a
        window of features.
        """
        if nW == 1:
            return seq2col_one(seq)
        else:  # pragma: no cover
            raise ValueError("Currently only nW=1 supported.")

    def backprop_seq2col(self, dY: ArrayT, nW: int) -> Array:
        if nW == 1:
            return backprop_seq2col_one(dY)
        else:  # pragma: no cover
            raise ValueError("Currently only nW=1 supported.")

    def gemm(
        self,
        x: Array2d,
        y: Array2d,
        out: Optional[Array2d] = None,
        trans1: bool = False,
        trans2: bool = False,
    ) -> Array2d:
        if trans1:
            x = x.T
        if trans2:
            y = y.T
        return self.xp.dot(x, y)

    def affine(self, X, W, b):
        return affine(X, W, b)

    def flatten(
        self,
        X: Sequence[ArrayT],
        dtype: Optional[DTypes] = None,
        pad: int = 0,
        ndim_if_empty: int = 2,
    ) -> ArrayT:
        if X is None or len(X) == 0:
            return self.alloc((0,) * ndim_if_empty, dtype=dtype or "f")
        X = [x for x in X if x.size != 0]
        if int(pad) >= 1:
            return flatten_with_padding(X, pad)
        else:
            result = self.xp.concatenate(X)

        result = self.xp.concatenate(X)
        if dtype is not None:
            result = self.xp.asarray(result, dtype=dtype)
        return result

    def unflatten(self, X: ArrayT, lengths: Array1d, pad: int = 0) -> List[ArrayT]:
        if not len(lengths):
            return []
        elif not X.size:
            empty_shape = (0,) + tuple(X.shape[1:])
            return [self.alloc(empty_shape) for _ in lengths]
        elif pad == 0:
            return unflatten_no_padding(X, self.asarray(lengths))
        else:
            return unflatten_with_padding(X, self.asarray(lengths), pad)

    def maxout(self, X):
        return maxout(X)

    def backprop_maxout(self, dY, which, P):
        return backprop_maxout(dY, which, P)

    def mish(self, X, threshold=20.0):
        return mish(X, threshold)

    def backprop_mish(
        self,
        dY: Array2d,
        X: Array2d,
        threshold: float = 20.0,
        out: Optional[Array2d] = None,
    ):
        return backprop_mish(dY, X, threshold)

    def relu(self, X, inplace=False):
        return relu(X)

    def backprop_relu(self, dY, Y, inplace=False):
        return backprop_relu(dY, Y)

    def update_averages(
        self, ema: Array, weights: Array, t: int, max_decay: float = 0.9999
    ) -> None:
        decay = (1.0 + t) / (10.0 + t)
        if decay > max_decay:
            decay = max_decay
        return update_averages(ema, weights, decay)

    def adam(
        self,
        weights: Array1d,
        gradient: Array1d,
        mom1: Array1d,
        mom2: Array1d,
        beta1: float,
        beta2: float,
        eps: float,
        learn_rate: float,
        mod_rate: float = 1.0,
    ) -> Tuple[Array1d, Array1d, Array1d, Array1d]:
        return adam(
            weights, gradient, mom1, mom2, beta1, beta2, eps, learn_rate * mod_rate
        )

    def clip_gradient(self, gradient: Array, threshold: float) -> Array:
        xp = self.xp
        grad_norm = xp.linalg.norm(gradient)
        if grad_norm >= threshold:
            gradient = gradient * (threshold / grad_norm)
        return gradient

    def logloss(self, y_true: Array, y_pred: Array):
        return logloss

    def sum_pool(self, X: Array2d, lengths: Array1d) -> Array2d:
        return sum_pool(X, lengths)

    def mean_pool(self, X: Array2d, lengths: Array1d) -> Array2d:
        return mean_pool(X, lengths)

    def max_pool(self, X: Array2d, lengths: Array1d) -> Tuple[Array2d, Array2d]:
        return max_pool(X, lengths)

    def backprop_sum_pool(self, d_sums: Array2d, lengths: Array1d) -> Array2d:
        return backprop_sum_pool(d_sums, lengths)

    def backprop_mean_pool(self, d_means: Array2d, lengths: Array1d) -> Array2d:
        return backprop_mean_pool(d_means, lengths)

    def backprop_max_pool(
        self, d_maxes: Array2d, which: Array2d, lengths: Array1d
    ) -> Array2d:
        return backprop_max_pool(d_maxes, which, lengths)

    def list2padded(self, seqs: List[Array2d]) -> Padded:
        """Pack a sequence of 2d arrays into a Padded datatype."""
        lengths: Array1d
        batch_size_at_t: Array1d 
        indices: Array1d
        if not seqs:
            empty = self.alloc_i1d(0)
            return Padded(self.alloc_f3d(0, 0, 0), empty, empty, empty)
        elif len(seqs) == 1:
            data = seqs[0].reshape((seqs[0].shape[0], 1) + seqs[0].shape[1:])
            batch_size_at_t = self.asarray([1] * data.shape[0], dtype="i")
            lengths = self.asarray([data.shape[0]], dtype="i")
            indices = self.asarray([0], dtype="i")
            return Padded(data, batch_size_at_t, lengths, indices)
        lengths_indices = [(len(seq), i) for i, seq in enumerate(seqs)]
        lengths_indices.sort(reverse=True)
        indices_ = [i for length, i in lengths_indices]
        lengths_ = [length for length, i in lengths_indices]
        nB = len(seqs)
        nS = max([len(seq) for seq in seqs])
        arr: Array3d = self.alloc_f3d(nB, nS, seqs[0].shape[1])
        for arr_i, (length, seqs_i) in enumerate(lengths_indices):
            arr = index_update(arr, index[arr_i, :length], self.asarray(seqs[seqs_i]))
        arr = self.as_contig(arr.transpose((1, 0, 2)))
        # Build a lookup table so we can find how big the batch is at point t.
        batch_size_at_t_ = numpy.zeros((nS,), dtype="i")
        batch_size_at_t_ += 1
        i = len(lengths_)
        for t in range(nS):
            if t == lengths_[i - 1]:
                i -= 1
                if i == 0:
                    break
            batch_size_at_t_[t] = i
        batch_size_at_t = self.asarray(batch_size_at_t_, dtype="i")
        lengths = self.asarray(lengths_, dtype="i")
        indices = self.asarray(indices_, dtype="i")
        return Padded(arr, batch_size_at_t, lengths, indices)

    def padded2list(self, padded: Padded) -> List[Array2d]:
        indices = padded.indices
        data = padded.data
        lengths = padded.lengths
        unpadded = [None] * len(lengths)
        data = self.as_contig(data.transpose((1, 0, 2)))
        for i in range(data.shape[0]):
            index_update(unpadded, index[indices[i]], data[i, : lengths[i]])
        return cast(List[Array2d], unpadded)

    def sigmoid(self, X: ArrayT, *, inplace: bool = False) -> ArrayT:
        return sigmoid(X)

    def dsigmoid(self, Y: ArrayT, *, inplace: bool = False) -> ArrayT:
        return Y * (1.0 - Y)

    def cosine(self, X: Array, Y: ArrayT) -> float:
        # Add a small constant to avoid 0 vectors
        X = X + 1e-8
        Y = Y + 1e-8
        normX = self.xp.linalg.norm(X, axis=1, keepdims=True)
        normY = self.xp.linalg.norm(Y, axis=1, keepdims=True)
        mul_norms = normX * normY
        cosine = (X * Y).sum(axis=1, keepdims=True) / mul_norms
        return cosine

    def cosine_abs_loss(
        self, X: Array, Y: ArrayT, *, ignore_zeros: bool = False
    ) -> float:
        cosine = self.cosine(X, Y)
        losses = self.xp.abs(cosine - 1)
        if ignore_zeros:
            # If the target was a zero vector, don't count it in the loss.
            zero_indices = self.xp.abs(Y).sum(axis=1) == 0
            losses[zero_indices] = 0
        loss = losses.sum()
        return loss

    def get_norm(self, X: Array) -> Array:
        norms = self.xp.linalg.norm(X, axis=1)
        norms[norms == 0] = 1
        return norms

    def dtanh(self, Y: ArrayT, *, inplace: bool = False) -> ArrayT:
        if inplace:
            Y **= 2
            Y *= -1.0
            Y += 1.0
            return Y
        else:
            return 1 - Y ** 2

    def softmax(self, x: Array, *, inplace: bool = False, axis: int = -1) -> Array:
        maxes = self.xp.max(x, axis=axis, keepdims=True)
        shifted = x - maxes
        new_x = self.xp.exp(shifted)
        new_x /= new_x.sum(axis=axis, keepdims=True)
        return new_x

    def softmax_sequences(
        self, Xs: Array2d, lengths: Array1d, *, inplace: bool = False, axis: int = -1
    ) -> Array2d:
        if Xs.ndim >= 3:
            err = f"Softmax currently only supports 2d. Got: {Xs.ndim}"
            raise NotImplementedError(err)
        # This loses almost no fidelity, and helps the numerical stability.
        Xs = self.xp.clip(Xs, -20.0, 20.0)
        new_x = self.xp.exp(Xs)
        summed = self.backprop_sum_pool(self.sum_pool(new_x, lengths), lengths)
        new_x /= summed
        return new_x

    def backprop_softmax(self, Y: Array, dY: Array, *, axis: int = -1) -> Array:
        dX = Y * dY
        dX -= Y * dX.sum(axis=axis, keepdims=True)
        return dX

    def backprop_softmax_sequences(
        self, dY: Array2d, Y: Array2d, lengths: Array1d
    ) -> Array2d:
        dX = Y * dY
        sum_dX = self.backprop_sum_pool(self.sum_pool(dX, lengths), lengths)
        dX -= Y * sum_dX
        return dX

    def recurrent_lstm(
        self, W: Array2d, b: Array1d, cells: Array2d,
        hiddens: Array2d, inputs: Array2d
    ) -> Tuple[Array2d, Array2d, Array3d]:
        return recurrent_lstm(W, b, cells, hiddens, inputs)

    def lstm(
        self, W: Array2d, b: Array1d, hidden_tm1: Array2d,
        cell_tm1: Array2d, inputs: Array3d
    ) -> Tuple[Array2d, Array2d, Array3d]:
        hiddens, cells, gates = lstm(W, b, cell_tm1, hidden_tm1, inputs)
        return hiddens, cells, gates

    def backprop_lstm(
        self,
        d_cells: Array2d,
        d_hiddens: Array2d,
        gates: Array3d,
        cells: Array2d,
        prevcells: Array2d,
    ) -> Tuple[Array3d, Array2d]:
        d_acts, d_prevcells = backprop_lstm(d_cells, d_hiddens, gates, cells, prevcells)
        return d_acts, d_prevcells

    def insert_into(self, shape, Xs):
        output = self.alloc(shape, dtype=Xs[0].dtype)
        for i, x in enumerate(Xs):
            output = index_update(output, index[i, :x.shape[0]], x)
        return output


class JaxRandom:
    """Perform randomization functions for Jax."""

    def shuffle(self, array):
        key = jax.random.PRNGKey(0)
        return jax.random.shuffle(key, array)

    def uniform(self, minval, maxval, shape):
        key = jax.random.PRNGKey(0)
        return jax.random.uniform(key, minval=0.0, maxval=1.0, shape=shape, dtype="f")

    def normal(self, scale, size):
        key = jax.random.PRNGKey(0)
        return jax.random.normal(key, shape=(size,)).astype("float32")


def jax_jit(*static_args) -> Wrapper:
    def wrapper(func: Callable) -> Callable:
        return jax.jit(func, static_argnums=static_args) if has_jax else func

    return wrapper


@jax_jit()
def seq2col_one(seq):
    # This is a test implementation that only supports nW=1
    nW = 1
    B = seq.shape[0]
    I = seq.shape[1]
    cols: Array3d = jax.numpy.zeros((B, (nW * 2 + 1), I))
    # Copy left contexts. The last words aren't the left-context for anything.
    cols = index_update(cols, index[nW:, :nW], seq[:-nW].reshape((-1, nW, I)))
    cols = index_update(cols, index[:, nW], seq)
    cols = index_update(cols, index[:-nW, nW + 1 :], seq[nW:].reshape((-1, nW, I)))
    return cols.reshape((B, I * (2 * nW + 1)))


@jax_jit()
def backprop_seq2col_one(dY):
    xp = jax.numpy
    nW = 1
    nF = nW * 2 + 1
    B = dY.shape[0]
    I = dY.shape[1] // nF
    dX = xp.zeros((B, I), dtype="f")
    dY = dY.reshape((B, nF, I))
    dX = index_update(dX, index[:-nW], dX[:-nW] + dY[nW:, :nW].reshape((-1, I)))
    dX += dY[:, nW]
    dX = index_update(dX, index[nW:], dX[nW:] + dY[:-nW, nW + 1 :].reshape((-1, I)))
    return dX


@jax_jit()
def affine(X, W, b):
    return X @ W.T + b


@jax_jit()
def relu(X):
    return X * (X > 0)


@jax_jit()
def backprop_relu(delta, signal_out):
    return delta * (signal_out > 0)


@jax_jit(1)
def flatten_with_padding(X, pad):
    xp = jax.numpy
    padded = []
    for x in X:
        padded.append(xp.zeros((pad,) + x.shape[1:], dtype=x.dtype))
        padded.append(x)
    padded.append(xp.zeros((pad,) + x.shape[1:], dtype=x.dtype))
    return xp.concatenate(padded)


def unflatten_no_padding(X, lengths):
    # Couldn't get the JIT version right here yet.
    start = 0
    unflat = []
    for length in lengths:
        unflat.append(X[start : start + length])
        start += length
    return unflat


def unflatten_with_padding(X, lengths, pad):
    # Couldn't get the JIT version right here yet.
    unflat = []
    for length in lengths:
        X = X[pad:]
        unflat.append(X[:length])
        X = X[length:]
    X = X[pad:]
    return unflat


@jax_jit()
def maxout(X):
    which = X.argmax(axis=-1)
    return X.max(axis=-1), which


@jax_jit(2)
def backprop_maxout(dY, which, P):
    dX = jax.numpy.zeros((dY.shape[0], dY.shape[1], P), dtype="float32")
    for b in range(dY.shape[0]):
        for o in range(dY.shape[1]):
            dX = index_update(dX, index[b, o, which[b, o]], dY[b, o])
    return dX


@jax_jit()
def adam(
    weights: Array1d,
    gradient: Array1d,
    mom1: Array1d,
    mom2: Array1d,
    beta1: float,
    beta2: float,
    eps: float,
    learn_rate: float,
) -> Tuple[Array, Array, Array, Array]:
    mom1 *= beta1
    mom2 *= beta2
    mom1 += gradient * (1.0 - beta1)
    mom2 += gradient * gradient * (1.0 - beta2)
    # Here we assume learn rate is calculated by the caller.
    # cdef weight_t a_t = learn_rate * sqrt(1-beta2**hp.t) / (1-beta1**hp.t);
    weights -= learn_rate * mom1 / (1.0 + eps)
    return weights, gradient, mom1, mom2


@jax_jit()
def update_averages(ema, weights, decay):
    return ema - (1 - decay) * (ema - weights)


@jax_jit()
def logloss(y_true: Array, y_pred: Array):
    log_yp = jax.numpy.log(y_pred + 1e-8)
    loss = (y_true * log_yp) + (1 - y_true) * jax.numpy.log((1 - y_pred) + 1e-8)
    return -loss


@jax_jit()
def sum_pool(X: Array2d, lengths: Array1d) -> Array2d:
    Y = jax.numpy.zeros((lengths.shape[0], X.shape[1]), dtype="f")
    start = 0
    for i, length in enumerate(lengths):
        Y = jax.ops.index_update(
            Y, jax.ops.index[i], X[start : start + length].sum(axis=0)
        )
        start += length
    return Y


@jax_jit()
def mean_pool(X: Array2d, lengths: Array1d) -> Array2d:
    Y = jax.numpy.zeros((lengths.shape[0], X.shape[1]), dtype="f")
    start = 0
    for i, length in enumerate(lengths):
        Y = jax.ops.index_update(
            Y, jax.ops.index[i], X[start : start + length].mean(axis=0)
        )
        start += length
    return Y


@jax_jit()
def max_pool(self, X: Array2d, lengths: Array1d) -> Array2d:
    Y = jax.numpy.zeros((lengths.shape[0], X.shape[1]), dtype="f")
    start = 0
    for i, length in enumerate(lengths):
        Y = jax.ops.index_update(
            Y, jax.ops.index[i], X[start : start + length].max(axis=0)
        )
        start += length
    return Y


@jax_jit()
def backprop_sum_pool(self, d_sums: Array2d, lengths: Array1d) -> Array2d:
    dX = self.alloc_f2d(lengths.sum(), d_sums.shape[1])
    start = 0
    for i, length in enumerate(lengths):
        dX[start : start + length] = d_sums[i]
        start += length
    return dX


@jax_jit()
def backprop_mean_pool(self, d_means: Array2d, lengths: Array1d) -> Array2d:
    dX = self.alloc_f2d(lengths.sum(), d_means.shape[1])
    start = 0
    for i, length in enumerate(lengths):
        dX[start : start + length] = d_means[i] / length
        start += length
    return dX


@jax_jit()
def backprop_max_pool(d_maxes: Array2d, which: Array2d, lengths: Array1d) -> Array2d:
    dX = numpy.jax.zeros((lengths.sum(), d_maxes.shape[1]))
    start = 0
    for i, length in enumerate(lengths):
        dX = index_update(dX, index[start : start + length, which[i]], d_maxes[i])
        start += length
    return dX


@jax_jit(1)
def mish(X: Array2d, threshold: float = 20.0) -> Array2d:
    Y = X * jax.numpy.tanh(jax.numpy.log(1.0 + jax.numpy.exp(X)))
    return jax.numpy.where(X >= threshold, X, Y)


@jax_jit(2)
def backprop_mish(X, dY, threshold=20.0):
    xp = jax.numpy
    exp_x = xp.exp(X)
    exp_2x = xp.exp(2 * X)
    exp_3x = xp.exp(3 * X)
    omega = (4.0 * (X + 1)) + (4 * exp_2x) + exp_3x + exp_x * (4.0 * X + 6)
    delta = 2.0 * exp_x + exp_2x + 2.0
    dX = dY * ((exp_x * omega) / (delta * delta))
    # Gradient when above threshold will ignore softplus.
    return jax.numpy.where(X >= threshold, dY, dX)


@jax_jit()
def sigmoid(X):
    return 1.0 / (1.0 + jax.numpy.exp(-X))


@jax_jit()
def dsigmoid(Y: ArrayT) -> ArrayT:
    return Y * (1.0 - Y)


@jax_jit()
def cosine(X: Array, Y: ArrayT) -> float:
    xp = jax.numpy
    # Add a small constant to avoid 0 vectors
    X = X + 1e-8
    Y = Y + 1e-8
    normX = xp.linalg.norm(X, axis=1, keepdims=True)
    normY = xp.linalg.norm(Y, axis=1, keepdims=True)
    mul_norms = normX * normY
    cosine = (X * Y).sum(axis=1, keepdims=True) / mul_norms
    return cosine


@jax_jit()
def dtanh(Y: ArrayT) -> ArrayT:
    return 1 - Y ** 2


@jax_jit(1)
def softmax(X: Array, axis: int) -> Array:
    xp = jax.numpy
    maxes = xp.max(X, axis=axis, keepdims=True)
    shifted = X - maxes
    new_x = xp.exp(shifted)
    new_x /= new_x.sum(axis=axis, keepdims=True)
    return new_x


@jax_jit(2)
def softmax_sequences(Xs: Array2d, lengths: Array1d, axis: int) -> Array2d:
    xp = jax.numpy
    # This loses almost no fidelity, and helps the numerical stability.
    Xs = xp.clip(Xs, -20.0, 20.0)
    new_x = xp.exp(Xs)
    summed = backprop_sum_pool(sum_pool(new_x, lengths), lengths)
    new_x /= summed
    return new_x


@jax_jit(2)
def backprop_softmax(Y: Array, dY: Array, axis: int) -> Array:
    dX = Y * dY
    dX -= Y * dX.sum(axis=axis, keepdims=True)
    return dX


@jax_jit(2)
def backprop_softmax_sequences(dY: Array2d, Y: Array2d, lengths: Array1d) -> Array2d:
    dX = Y * dY
    sum_dX = backprop_sum_pool(sum_pool(dX, lengths), lengths)
    dX -= Y * sum_dX
    return dX


@jax_jit()
def recurrent_lstm(W, b, c0, h0, X):
    """
    X: Inputs
    Y: Outputs (aka hiddens)
    C: Cells
    G: Gates (Output of non-linearity, i.e. lstm_gates(X @ W.T)
    A: Activations (X @ W.T, before non-linearity)
    """
    xp = jax.numpy
    nL, nB, nI = X.shape
    nO = hidden.shape[1]
    # Preallocate these so we can pass them through for loop.
    Y = xp.zeros((nL+1, nB, nO), dtype="f")
    G = xp.zeros((nL, nB, nO, 4), dtype="f")
    C = xp.zeros((nL+1, nB, nO), dtype="f")
    # Set initial hidden and cell states. The Y and C will be shifted 1,
    # so that we can have fewer arrays.
    Y = index_update(Y, index[0], h0)
    C = index_update(C, index[0], c0)
    state = ((W, b, X), (Y, C, G))
    state = jax.lax.fori_loop(0, X.shape[0], _lstm_stepper, state)
    (W, b, X), (Y, C, G) = state 
    # Return from the first, to remove the initial state state. We do need the
    # C[0] though, for the backprop.
    return Y[1:], C, G


@jax_jit()
def _lstm_stepper(t, state):
    (W, b, X), (Y, C, G) = state 
    yt, ct, gt = lstm(W, b, Y[t], C[t], X[t])
    # The offsets here are a bit unintuitive, because Y and C are 1-offset.
    Y = index_update(Y, index[t+1], yt)
    C = index_update(C, index[t+1], ct)
    G = index_update(G, index[t], gt)
    return (W, b, X), (Y, C, G)


@jax_jit()
def lstm(W, b, Yt1, Ct1, Xt):
    xp = jax.numpy
    nB, nO = Yt1.shape[0]
    nI = Xt.shape[0]
    At = xp.dot(xp.hstack((Xt, Yt1)), W.T) + b
    At = At.reshape((nB, nO, 4))
    hf, hi, ho, hc = xp.split(At, 4, axis=-1)
    # Need 'gates' here, the transformed acts, for backward pass.
    hf = sigmoid(hf.reshape((nB, nO)))
    hi = sigmoid(hi.reshape((nB, nO)))
    ho = sigmoid(ho.reshape((nB, nO)))
    hc = xp.tanh(hc.reshape((nB, nO)))

    Ct = (hf * Ct1) + (hi * hc)
    Yt = xp.tanh(Ct) * ho
    Gt = xp.concatenate((hf, hi, ho, hc), axis=-1)
    return Yt, Ct, Gt


@jax_jit()
def backprop_recurrent_lstm(dY, dC, dG, W, b, G, C, X):
    xp = jax.numpy
    nL, nB, nI = X.shape
    nO = hidden.shape[1]
    # Preallocate these so we can pass them through for loop.
    dX = xp.zeros((nL, nB, nI), dtype="f")
    dW = xp.zeros(W.shape, dtype="f")
    db = xp.zeros(b.shape, dtype="f")
    state = (
        (dW, db, dX),
        (dY, dC),
        (G, C, X),
        (W, b)
    )
    state = jax.lax.fori_loop(X.shape[0]-1, -1, _backprop_lstm_stepper, state)
    (dW, db, dX), (dY, dC), (G, C, X), (W, b) = state
 
    return dW, db, dX


@jax_jit()
def _backprop_lstm_stepper(t, state):
    (dW, db, dX), (dY, dCt1), (G, C, X), (W, b) = state
    # TODO: I thnk I'm missing a d_hiddens + dY[t] here? I think I'm ignoring
    # the sequence grads.
    # TODO: Check this is right with the state nums.
    (dAt, dCt) = backprop_lstm(dCt1, dY[t+1], G[t], C[t+1], C[t])
    dXt, dYt = (dAt @ W).split(nO, axis=-1)
    dW += dAt.T @ X[t]
    db += dAt.sum(axis=0)
    dX = index_update(dX, index[t], dXt)
    return (dW, db, dX), (dY, dC), (G, C, X), (W, b)


@jax_jit()
def backprop_lstm(
    d_cells,
    d_hiddens,
    gates,
    cells,
    prevcells,
) -> Tuple[Array3d, Array2d]:
    xp = jax.numpy
    hf = gates[:, :, 0]
    hi = gates[:, :, 1]
    ho = gates[:, :, 2]
    hc = gates[:, :, 3]
    # Gradient for ho and c in h = sigmoid(ho) * tanh(c)
    d_ho = xp.tanh(cells) * d_output * dsigmoid(ho)
    d_prevcells = ho * d_output * dtanh(xp.tanh(cells))
    d_prevcells += d_cells  # Carry gradient from timestep
    # Gradient for hf, hi, hc, prev[i]
    # in c = sigmoid(hf) * prev[i] + sigmoid(hi) * tanh(hc)
    d_hf = dsigmoid(hf) * d_prevcells * prev
    d_hi = dsigmoid(hi) * d_prevcells * hc
    d_hc = dtanh(hc) * d_prevcells * hi
    d_prev = d_prevcells * hf
    d_acts = xp.concatenate((d_hf, d_hi, d_ho, d_hc), axis=-1)
    return d_acts, d_prevcells

JaxOps.xp.random = JaxRandom()
JaxOps.xp.testing = numpy.testing

if has_jax:
    jax.tree_util.register_pytree_node(
        JaxOps, lambda ops: ([], None), lambda info, values: JaxOps()
    )

__all__ = ["JaxOps", "has_jax"]
