from . import s4
from functools import partial
import jax
import jax.numpy as np
from flax import linen as nn
from jax.nn.initializers import lecun_normal, normal


if __name__ == '__main__':
    rng = jax.random.PRNGKey(1)


def complex_softmax(x, eps=1e-7):
    def reciprocal(x):
        return x.conj() / (x * x.conj() + eps)

    x2 = x - x[np.argmax(x.real)]
    e = np.exp(x2)
    return e * reciprocal(np.sum(e))


def dss_kernel(W, Lambda, L, step):
    P = (step * Lambda)[:, None] * np.arange(L)
    S = jax.vmap(complex_softmax)(P)
    return ((W / Lambda) @ S).ravel().real


def dss_ssm(W, Lambda, L, step):
    N = Lambda.shape[0]
    Abar = np.diag(np.exp(Lambda * step))
    b = jax.vmap(lambda l: 1 / (l * (np.exp(l * np.arange(L) * step)).sum()))
    Bbar = b(Lambda).reshape(N, 1)
    Cbar = W.reshape(1, N)
    return Abar, Bbar, Cbar


class DSSLayer(nn.Module):
    N: int
    l_max: int
    decode: bool = False

    lr = {
        "Lambda_re": 0.1,
        "Lambda_im": 0.1,
        "log_step": 0.1,
    }

    def _init__(self):
        super().__init__()
        # Learned Parameters
        hippo_Lambda_real_initializer, hippo_Lambda_imag_initializer, hippo_p_initializer, hippo_B_initializer = s4.hippo_initializer(self.N)
        self.Lambda_re = self.param("Lambda_re", hippo_Lambda_real_initializer, (self.N,))
        self.Lambda_im = self.param("Lambda_im", hippo_Lambda_imag_initializer, (self.N,))
        self.Lambda = self.Lambda_re + 1j*self.Lambda_im
        self.W = self.param("W", normal(stddev=.5**.5), (1, self.N, 2))
        self.W = self.W[..., 0] + 1j * self.W[..., 1]
        self.D = self.param("D", nn.initializers.ones, (1,))
        self.step = np.exp(
            self.param("log_step", s4.log_step_initializer(), (1,))
        )
        if not self.decode:
            self.K = dss_kernel(self.W, self.Lambda, self.l_max, self.step)
        else:
            # FLAX code to ensure that we only compute discrete once during decoding.
            def init_discrete():
                return dss_ssm(self.W, self.Lambda, self.l_max, self.step)
            ssm_var = self.variable("prime", "ssm", init_discrete)
            if self.is_mutable_collection("prime"):
                ssm_var.value = init_discrete()
            self.ssm = ssm_var.value

            # RNN Cache
            self.x_k_1 = self.variable(
                "cache", "cache_x_k", np.zeros, (self.N,), np.complex64
            )

    def __call__(self, u):
        if not self.decode:
            return s4.causal_convolution(u, self.K) + self.D * u
        else:
            x_k, y_s = s4.scan_SSM(*self.ssm, u[:, np.newaxis], self.x_k_1.value)
            if self.is_mutable_collection("cache"):
                self.x_k_1.value = x_k
            return y_s.reshape(-1).real + self.D * u

DSSLayer = s4.cloneLayer(DSSLayer)
